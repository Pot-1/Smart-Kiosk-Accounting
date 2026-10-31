from pathlib import Path
import os


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DATABASE_URL = os.getenv("SKA_DATABASE_URL", f"sqlite:///{DATA_DIR / 'smart_kiosk.db'}")
APP_NAME = "Smart Kiosk Accounting"
DEFAULT_BUSINESS_NAME = "MARIOS FROZEN FOODS"
TIMEZONE = "Africa/Lagos"
INVENTORY_METHOD = os.getenv("SKA_INVENTORY_METHOD", "FIFO").upper()
DEMO_MODE = os.getenv("SKA_DEMO_MODE", "").strip().lower() in {"1", "true", "yes"}
SESSION_LIFETIME_HOURS = int(os.getenv("SKA_SESSION_LIFETIME_HOURS", "12"))
REMEMBERED_SESSION_DAYS = int(os.getenv("SKA_REMEMBERED_SESSION_DAYS", "30"))
PASSWORD_RESET_MINUTES = int(os.getenv("SKA_PASSWORD_RESET_MINUTES", "30"))
PUBLIC_APP_URL = os.getenv("SKA_PUBLIC_URL", "http://localhost:8501").rstrip("/")
