import hashlib
import hmac
import secrets

PASSWORD_HASH_ITERATIONS = 600_000
LEGACY_PASSWORD_HASH_ITERATIONS = 310_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, PASSWORD_HASH_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        if stored_hash.startswith("pbkdf2_sha256$"):
            algorithm, rounds, salt_hex, digest_hex = stored_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            iterations = int(rounds)
            if not 100_000 <= iterations <= 2_000_000:
                return False
        else:
            salt_hex, digest_hex = stored_hash.split(":", 1)
            iterations = LEGACY_PASSWORD_HASH_ITERATIONS
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), iterations
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def password_hash_needs_upgrade(stored_hash: str) -> bool:
    if not stored_hash.startswith("pbkdf2_sha256$"):
        return True
    try:
        _, rounds, _, _ = stored_hash.split("$", 3)
        return int(rounds) < PASSWORD_HASH_ITERATIONS
    except ValueError:
        return True
