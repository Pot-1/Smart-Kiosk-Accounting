# Smart Kiosk Accounting

A local-first Python accounting and stock application for small Nigerian businesses. The first release is built with Streamlit, SQLite, SQLAlchemy, Decimal-safe Naira inputs, and a double-entry journal. Reports are derived from posted journal entries.

## Run it

Use Python 3.12 or later:

```powershell
cd Smart-Kiosk-Accounting
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

The SQLite database is initialized automatically at `data/smart_kiosk.db`. To choose another database path, set `SKA_DATABASE_URL` before starting the application. SQLite is the default; the SQLAlchemy models can be used with a PostgreSQL connection URL when deploying.

## Demo sign-in

- Username: `admin`
- Password: `kiosk1234`

Change the demo password in **Settings** after the first sign-in. For a fresh database, set `SKA_ADMIN_USERNAME` and `SKA_ADMIN_PASSWORD` before starting the app.

The demo business is **MARIOS FROZEN FOODS**. It starts with ₦1,500,000 opening capital, 100 chickens purchased at ₦8,000 each plus ₦15,000 transport, and example cash and credit sales, a customer payment, a supplier payment, and missing/damaged-stock adjustments. All posted entries are double-entry balanced. The database is local to this machine unless you configure a shared database.

## What works

- Record purchases, including transport/loading costs, and cash, bank, POS, or credit sales.
- Attach optimized receipt photos to purchases, sales, and expenses, and print sales receipts as PDF.
- Keep batch-level stock and prevent sales or adjustments from creating negative stock.
- Choose FIFO, LIFO, or weighted-average inventory costing; FIFO is the default.
- Record expenses, owner capital/drawings, customer and supplier payments, sales returns, purchase returns, and stock losses.
- Automatically post balanced journals, update cash/bank, inventory, customer/supplier balances, and retain an audit entry for posted transactions.
- Review the dashboard, inventory, customer/supplier balances, journals, ledgers, trial balance, trading account, profit and loss, balance sheet, and export statements as PDF or Excel.
- Import bank statements from CSV/Excel and reconcile exact-amount bank-book entries.
- Change between everyday-language navigation and accountant tools.

Amounts are stored as integer kobo in the ledger and converted to/from `Decimal` at input/report boundaries. The application currently records whole-unit quantities (pieces, cartons, packs, etc.); fractional-weight stock, multi-user access controls, tax/VAT workflows, and production-grade hosted deployment are not part of this first release. Back up `data/smart_kiosk.db` regularly.

## Tests

From this directory:

```powershell
python -m unittest discover -s tests -v
```
