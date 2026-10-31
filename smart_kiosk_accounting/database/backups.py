import hashlib
import logging
import os
import sqlite3
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import boto3
from cryptography.fernet import Fernet, InvalidToken

from config import DATA_DIR
from database.database import SessionLocal, engine
from database.models import BackupRecord

logger = logging.getLogger(__name__)
_scheduler_lock = threading.Lock()
_scheduler_started = False


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_root() -> Path:
    root = Path(os.getenv("SKA_BACKUP_DIR", str(DATA_DIR / "backups"))).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _backend(database_engine=engine) -> str:
    return "sqlite" if database_engine.dialect.name == "sqlite" else "postgresql"


def _database_file(database_engine=engine) -> Path:
    database_path = database_engine.url.database
    if not database_path or database_path == ":memory:":
        raise RuntimeError("File-based SQLite is required for local backups.")
    return Path(unquote(database_path)).resolve()


def _dump_postgresql(destination: Path, database_engine=engine) -> None:
    url = database_engine.url
    if not url.host or not url.database:
        raise RuntimeError("The PostgreSQL URL must include a host and database.")
    command = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--file",
        str(destination),
        "--host",
        url.host,
        "--port",
        str(url.port or 5432),
        "--username",
        unquote(url.username or ""),
        "--dbname",
        unquote(url.database),
    ]
    environment = os.environ.copy()
    if url.password:
        environment["PGPASSWORD"] = unquote(url.password)
    subprocess.run(
        command, check=True, capture_output=True, text=True, env=environment
    )


def verify_backup(path: str | Path, backend: str | None = None) -> bool:
    backup_path = Path(path).resolve()
    if not backup_path.is_file() or backup_path.stat().st_size == 0:
        raise ValueError("The backup file is missing or empty.")
    backend = backend or (
        "postgresql" if backup_path.suffix == ".dump" else "sqlite"
    )
    if backend == "sqlite":
        connection = None
        try:
            connection = sqlite3.connect(
                f"file:{backup_path}?mode=ro", uri=True
            )
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise ValueError("The SQLite backup failed its integrity check.")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError(
                    "The SQLite backup contains foreign-key violations."
                )
        except sqlite3.DatabaseError as exc:
            raise ValueError(
                "The SQLite backup failed its integrity check."
            ) from exc
        finally:
            if connection is not None:
                connection.close()
    elif backend == "postgresql":
        result = subprocess.run(
            ["pg_restore", "--list", str(backup_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            raise ValueError("The PostgreSQL backup contains no entries.")
    else:
        raise ValueError(f"Unsupported backup backend: {backend}")
    return True


def _record_backup(
    path: Path, backend: str, verified: bool
) -> BackupRecord:
    checksum = file_sha256(path)
    with SessionLocal() as session:
        session.info["system_operation"] = True
        record = BackupRecord(
            file_name=path.name,
            file_path=str(path),
            backend=backend,
            size_bytes=path.stat().st_size,
            checksum_sha256=checksum,
            verified=verified,
        )
        session.add(record)
        session.commit()
        return record


def create_backup(
    *, upload_offsite: bool = False, save_history: bool = True,
    target_engine=engine,
) -> BackupRecord | Path:
    backend = _backend(target_engine)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = ".sqlite3" if backend == "sqlite" else ".dump"
    path = _backup_root() / f"smart-kiosk-{now}-{uuid.uuid4().hex[:8]}{suffix}"
    if backend == "sqlite":
        source = sqlite3.connect(str(_database_file(target_engine)))
        try:
            destination = sqlite3.connect(str(path))
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
    else:
        _dump_postgresql(path, target_engine)

    verified = verify_backup(path, backend)
    if not save_history:
        return path
    record = _record_backup(path, backend, verified)
    if upload_offsite:
        _upload_offsite(path, record)
    return record


def _upload_offsite(path: Path, record: BackupRecord) -> None:
    bucket = os.getenv("SKA_BACKUP_S3_BUCKET", "").strip()
    kms_key = os.getenv("SKA_BACKUP_S3_KMS_KEY", "").strip()
    if not bucket or not kms_key:
        raise RuntimeError(
            "Off-site backup requires SKA_BACKUP_S3_BUCKET and "
            "SKA_BACKUP_S3_KMS_KEY."
        )
    encryption_key = os.getenv("SKA_BACKUP_ENCRYPTION_KEY", "").strip()
    content = path.read_bytes()
    if encryption_key:
        try:
            content = Fernet(encryption_key.encode("ascii")).encrypt(content)
        except (ValueError, TypeError) as exc:
            raise ValueError("SKA_BACKUP_ENCRYPTION_KEY is not a valid Fernet key.") from exc
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=bucket,
        Key=path.name,
        Body=content,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=kms_key,
        Metadata={"sha256": hashlib.sha256(content).hexdigest()},
    )
    with SessionLocal() as session:
        session.info["system_operation"] = True
        stored = session.get(BackupRecord, record.id)
        if stored is not None:
            stored.offsite = True
            session.commit()


def download_offsite_backup(record: BackupRecord) -> Path:
    if not record.offsite:
        raise ValueError("This backup is not available in off-site storage.")
    if Path(record.file_name).name != record.file_name:
        raise ValueError("The stored backup filename is invalid.")
    bucket = os.getenv("SKA_BACKUP_S3_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("SKA_BACKUP_S3_BUCKET is not configured.")
    response = boto3.client("s3").get_object(Bucket=bucket, Key=record.file_name)
    content = response["Body"].read()
    encryption_key = os.getenv("SKA_BACKUP_ENCRYPTION_KEY", "").strip()
    if encryption_key:
        try:
            content = Fernet(encryption_key.encode("ascii")).decrypt(content)
        except (InvalidToken, ValueError, TypeError) as exc:
            raise ValueError("The off-site backup could not be decrypted.") from exc
    if hashlib.sha256(content).hexdigest() != record.checksum_sha256:
        raise ValueError("The off-site backup checksum does not match its history.")
    path = _backup_root() / record.file_name
    path.write_bytes(content)
    verify_backup(path, record.backend)
    return path


def restore_sqlite_backup(path: str | Path) -> None:
    backup_path = Path(path).resolve()
    verify_backup(backup_path, "sqlite")
    database_path = _database_file()
    if backup_path == database_path:
        raise ValueError("A backup cannot be restored over itself.")
    create_backup()
    temporary_path = database_path.with_suffix(
        database_path.suffix + ".restore"
    )
    if temporary_path.exists():
        temporary_path.unlink()
    source = sqlite3.connect(str(backup_path))
    destination = sqlite3.connect(str(temporary_path))
    try:
        source.backup(destination)
    finally:
        source.close()
        destination.close()
    verify_backup(temporary_path, "sqlite")
    engine.dispose()
    os.replace(temporary_path, database_path)


def restore_postgresql_backup(path: str | Path, *, confirmed: bool = False) -> None:
    if not confirmed:
        raise PermissionError("Explicit confirmation is required to restore PostgreSQL.")
    backup_path = Path(path).resolve()
    verify_backup(backup_path, "postgresql")
    create_backup()
    url = engine.url
    if not url.host or not url.database:
        raise RuntimeError("The PostgreSQL URL must include a host and database.")
    command = [
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-acl",
        "--host",
        url.host,
        "--port",
        str(url.port or 5432),
        "--username",
        unquote(url.username or ""),
        "--dbname",
        unquote(url.database),
        str(backup_path),
    ]
    environment = os.environ.copy()
    if url.password:
        environment["PGPASSWORD"] = unquote(url.password)
    subprocess.run(
        command, check=True, capture_output=True, text=True, env=environment
    )


def start_backup_scheduler() -> None:
    global _scheduler_started
    interval_hours = int(os.getenv("SKA_BACKUP_INTERVAL_HOURS", "24"))
    if interval_hours < 1:
        raise ValueError("SKA_BACKUP_INTERVAL_HOURS must be at least one.")
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def run_periodically():
        timer = threading.Timer(interval_hours * 3600, run_periodically)
        timer.daemon = True
        timer.start()
        try:
            create_backup(
                upload_offsite=bool(os.getenv("SKA_BACKUP_S3_BUCKET", "").strip())
            )
        except Exception:
            logger.exception("Scheduled database backup failed")

    timer = threading.Timer(interval_hours * 3600, run_periodically)
    timer.daemon = True
    timer.start()
