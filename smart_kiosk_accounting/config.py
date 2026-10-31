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
