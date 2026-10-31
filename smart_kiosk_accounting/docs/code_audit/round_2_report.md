# Round 2 — Isolation Boundaries and Adversarial Follow-up

## Findings and changes

- The independent Round 1 adversarial recheck demonstrated that a purchase
  whose stock and journal had already been reversed could still be returned.
  `record_purchase_return` now accepts only posted purchases. A regression test
  verifies the rejected return leaves stock unchanged.
- Added synthetic database tests that attempt cross-business references across
  transaction items, journal entries and lines, stock batches and movements,
  payments, expenses, returns, stock adjustments, bank statements, audit logs,
  receipts, products/categories, and stock counts/count lines.
- The previously reported onboarding permission, split supplier return,
  selected FIFO source layer, and inventory aggregation fixes remain covered.

## Verification

- Full suite: **54 tests passed** (`python -m unittest discover -s tests -q`).
- `compileall`: passed.
- `git diff --check`: passed.
- `pip check`: passed, no broken requirements.
- New tests use only synthetic/in-memory SQLite databases; no local database or
  backup files were accessed.

## Round 2 score

The independent Round 2 score was **84/100**, up from **82/100** in Round 1.
The scorer credited the reversed-purchase guard and broader composite
foreign-key tests while noting that its test execution was not independently
verified and that PostgreSQL and static-analysis evidence remain unavailable.

| Category | Score | Evidence and remaining gap |
|---|---:|---|
| Correctness and functionality | 17/20 | Reversed purchases are rejected before stock changes; unusual histories remain. |
| Code quality and maintainability | 11/15 | Table-driven FK tests improve clarity; service modularization remains. |
| Security | 12/15 | Role and tenant controls reviewed; deployment settings not certified. |
| Database integrity | 9/10 | Composite cross-business references tested on synthetic SQLite; aggregate journal constraints remain application-level. |
| Accounting accuracy | 14/15 | Return/reversal scenarios tested; rounding and settlements need broader coverage. |
| Performance | 8/10 | Fixed inventory page query count; no production benchmark. |
| Testing and reliability | 9/10 | Reported 54 tests pass; no coverage/static analysis. |
| Documentation | 4/5 | README and round reports updated; operator guidance can improve. |

## Remaining gaps

- Concurrent duplicate submissions and returns, auth expiry boundaries, and
  corrupted-ledger detection still lack focused tests.
- PostgreSQL and managed/off-site backup paths are not exercised.
- Ruff, mypy, pytest, coverage, and pip-audit are unavailable in this
  environment.
