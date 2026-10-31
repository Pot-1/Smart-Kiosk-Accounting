from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE


def lagos_now() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)


def new_reference(prefix: str) -> str:
    return f"{prefix}-{lagos_now():%Y%m%d-%H%M%S-%f}"
