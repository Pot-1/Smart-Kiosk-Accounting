# Initial Code Audit

## Scope and method

Reviewed the local Smart Kiosk Accounting checkout on branch
`audit/multi-agent-quality-improvement`. The audit covered accounting and
transaction services, inventory valuation, authentication and authorization,
tenant enforcement, migrations, Streamlit pages, tests, dependencies, and
documentation. Security review was performed independently. Tests used
synthetic or in-memory databases; the ignored local database and backups were
not opened, read, migrated, or modified. Their names and file metadata were
visible during repository-status checks.

## Baseline evidence

- Baseline test suite: **47 tests passed** (`python -m unittest discover -s
  tests -q`).
- Independent baseline quality score: **74/100**.
- Baseline findings were based on the checked-out source and test coverage; no
  claim is made that every deployment configuration or backend was exercised.

## Confirmed findings

| Severity | Area | Finding | Correction |
|---|---|---|---|
| Medium | Authorization | A member without `business_settings` permission could enter incomplete business onboarding and post opening balances. | Enforce the permission at the onboarding handler before reading or mutating business data. |
| Medium | Supplier returns | Split-tender purchases were returned entirely to the supplied refund method, omitting the credit portion and overstating supplier balances. | Allocate returns between open supplier credit and a selected cash/bank/POS refund; record the credit portion in the return transaction. |
| Medium | Inventory costing | FIFO purchase returns physically selected the original receipt but valued the return from the oldest product layer, which could be a different receipt. | Prefer the selected source-item valuation layer when available, retaining FIFO fallback for remaining quantity. |
| Low | Inventory page performance | `product_stock_frame` issued separate stock-quantity and stock-value queries per product. | Aggregate batch quantities and values by product with one grouped query. |

## Independent baseline score

| Category | Score | Maximum |
|---|---:|---:|
| Correctness and functionality | 14 | 20 |
| Code quality and maintainability | 11 | 15 |
| Security | 11 | 15 |
| Database integrity | 8 | 10 |
| Accounting accuracy | 12 | 15 |
| Performance | 6 | 10 |
| Testing and reliability | 8 | 10 |
| Documentation | 4 | 5 |
| **Total** | **74** | **100** |

The score reflects observable implementation and test evidence, not a
production-readiness certification. The independent scorer identified a large
transaction service, limited performance benchmarks, and untested edge cases
as areas to improve.

## Known validation limitations

The repository's test runner is `unittest`. Ruff, mypy, pytest, coverage, and
pip-audit executables were not available in the active environment. PostgreSQL,
off-site backup failure paths, and deployment-specific settings were not
exercised. Tax/VAT workflows are described in the README as outside the
current release scope.
