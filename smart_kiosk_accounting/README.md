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

## Accounts and first-time setup

On a new installation, select **Create account** to register with your name, email, Nigerian phone number, username, password, and business details. Registration creates the business and owner account. A guided setup then covers business details and type, opening balances, products, stock, and suppliers. Optional steps can be skipped and setup can be completed later.

Sign in with a username or email address. Passwords are stored as salted PBKDF2 hashes. Login attempts are rate-limited, accounts are temporarily locked after repeated failures, and session cookies expire. **Remember this device** extends the session up to 30 days; otherwise it expires after 12 hours. Configure `SKA_PUBLIC_URL` to your HTTPS application address when deployed.

Password recovery requires SMTP configuration. Set `SKA_SMTP_HOST`, `SKA_SMTP_PORT` (default `587`), `SKA_SMTP_USER`, `SKA_SMTP_PASSWORD`, and `SKA_SMTP_FROM` in the application environment. Set `SKA_PUBLIC_URL` to the public HTTPS address so reset links point back to the deployed app. Never commit SMTP credentials.

## Team roles

Owners can add team accounts and configure permissions in **Settings**. Available roles are **OWNER**, **MANAGER**, **CASHIER**, **ACCOUNTANT**, and **STOCK MANAGER**. New team members receive an initial password from the owner and can change it after signing in. Owners retain full access; other role permissions are configurable per business.

## Optional demo data

Production mode does not create a default administrator. To explicitly seed a fresh development database with demo data, enable demo mode before starting the app:

```powershell
$env:SKA_DEMO_MODE = "true"
$env:SKA_ADMIN_USERNAME = "admin"
$env:SKA_ADMIN_PASSWORD = "set-a-unique-demo-password"
streamlit run app.py
```

Demo mode creates **MARIOS FROZEN FOODS** with opening capital, stock, and sample transactions. The demo administrator is only seeded when `SKA_DEMO_MODE` is enabled. Do not use demo credentials in production.

Existing databases are migrated automatically with Alembic when the app starts. A verified pre-migration backup is created first. Existing users, password hashes, transactions, journals, and stock records are preserved; legacy users receive a membership for their existing business. The migration removes global username, business-name, and transaction-reference uniqueness, replacing it with business-scoped uniqueness. Schema downgrade is intentionally unsupported; recover by restoring a verified pre-migration backup.

## Multiple businesses, isolation, and backups

Each user is assigned to one or more businesses through a business membership. A user's active business is selected in the sidebar. Usernames are unique within a business; email addresses identify the global login account. ORM reads and writes require an authenticated active business, service writes re-check the user's membership and role permission, and tenant-owned foreign keys prevent cross-business references. Receipt images and reports use the same active-business scope.

Important account and business changes, permission changes, transaction postings, login, and logout are written to the business audit log.

SQLite backups are created automatically once every `SKA_BACKUP_INTERVAL_HOURS` (default `24`) and before migrations. Configure `SKA_BACKUP_DIR` to choose another local backup directory. To enable the **Settings → Database backups** controls, set `SKA_BACKUP_ADMIN_USER_IDS` to a comma-separated list of trusted platform administrator user IDs. Those controls create and verify backups, show history, download backups, and restore a verified SQLite backup after first making a safety backup.

For hosted PostgreSQL, use a SQLAlchemy URL such as `postgresql+psycopg://...`. Install `pg_dump` and `pg_restore` on the application host for verified backups. For encrypted off-site S3 backups, configure `SKA_BACKUP_S3_BUCKET`, `SKA_BACKUP_S3_KMS_KEY`, and host IAM credentials; S3 KMS encryption is required. Optionally set `SKA_BACKUP_ENCRYPTION_KEY` to a Fernet key for client-side encryption as well. Off-site backups are uploaded by the scheduled/manual backup flow.

The Alembic migration is forward-only because reversing a live tenant ownership change is unsafe. A restore requires a verified backup and replaces the current SQLite database; PostgreSQL restoration should be performed by an operator with `pg_restore` after verifying the destination.

## What works

- Record purchases, including transport/loading costs, and cash, bank, POS, or credit sales.
- Attach optimized receipt photos to purchases, sales, and expenses, and print sales receipts as PDF.
- Keep batch-level stock, prevent concurrent sales from overselling, and use FEFO to pick expiring stock.
- Value inventory using FIFO or weighted average. LIFO is rejected for financial inventory valuation.
- Record expenses, owner capital/drawings, customer and supplier payments, sales returns, purchase returns, and stock losses.
- Record physical stock counts, compare counted and recorded quantities, and require a reason for each discrepancy adjustment.
- Post each operation atomically with balanced journals; keyed submissions are idempotent, and reuse of a key with a different request is rejected.
- Reverse eligible transactions with contra entries instead of deleting financial history. Reversals are blocked when subsequent stock movements prevent a safe stock reversal.
- Reconcile inventory quantities and values to movements and the inventory ledger, and customer/supplier subledgers to their control accounts.
- Review the dashboard, inventory, customer/supplier balances, journals, ledgers, trial balance, trading account, profit and loss, balance sheet, and export statements as PDF or Excel.
- Import bank statements from CSV/Excel and reconcile exact-amount bank-book entries.
- Change between everyday-language navigation and accountant tools.

Amounts are stored as integer kobo in the ledger and converted to/from `Decimal` at input/report boundaries. Stock quantities support fractional kilograms and grams; discrete units such as pieces, cartons, and packs must be whole. Returns are linked to their original transaction lines so quantities and selling/refund prices are tracked per line, including repeated products sold at different prices. Back up `data/smart_kiosk.db` regularly.

## Tests

From this directory:

```powershell
python -m unittest discover -s tests -v
```
