# Round 1 — Initial Audit and Repairs

## Changes

- `pages/onboarding.py`: require the active member's `business_settings`
  permission before onboarding can read or mutate business setup or opening
  balances. The check is in the page handler, not just navigation.
- `transactions/service.py`: calculate purchase-return credit reversal from the
  original credit share, prior returns, and supplier's current open balance;
  post any remaining refund to the selected cash/bank/POS account.
- `inventory/stock_manager.py`: for FIFO purchase returns, consume available
  valuation quantity from the selected original purchase-item layer before
  falling back to other FIFO layers.
- `pages/common.py`: aggregate stock quantity and value by product in one
  query instead of issuing two stock queries for every product.
- Tests cover cashier onboarding authorization, split and settled supplier
  returns, selected FIFO purchase-return costing, and a fixed inventory-page
  query count.

## Verification

- Full suite: **52 tests passed** (`python -m unittest discover -s tests -q`).
- `compileall`: passed.
- `git diff --check`: passed.
- `pip check`: passed, no broken requirements.
- A disposable SQLite database upgraded to Alembic head in the prior
  verification; no live database was migrated.
- Ruff, mypy, pytest, coverage, and pip-audit were unavailable in the active
  environment.

## Round 1 score

The independent Round 1 score was **82/100**, up from the initial
**74/100**. Improvements were supported by the new authorization, supplier
return, FIFO cost-layer, and query-count tests. The independent scorer noted
that broader settlement/rounding tests, production configuration review,
PostgreSQL validation, performance benchmarks, and static analysis remain
unverified.

| Category | Score | Evidence and remaining gap |
|---|---:|---|
| Correctness and functionality | 16/20 | Purchase-return tender allocation and service authorization were tested; more settlement and rounding cases remain. |
| Code quality and maintainability | 11/15 | New tests are targeted; transaction service remains large. |
| Security | 12/15 | Onboarding permission gate and cashier denial test; deployment configuration not certified. |
| Database integrity | 8/10 | Existing tenant constraints retained; PostgreSQL not exercised. |
| Accounting accuracy | 14/15 | Return credit allocation and FIFO selected-layer cases added; more complex histories remain. |
| Performance | 8/10 | Inventory page query count is fixed; other reconciliation scans remain. |
| Testing and reliability | 9/10 | Reported 52 tests plus compilation/diff/dependency checks; no coverage/static analysis. |
| Documentation | 4/5 | README and audit reports updated; operator troubleshooting remains limited. |

## Outstanding risks

- Concurrent duplicate submissions, concurrent return attempts, and precise
auth-token expiry boundaries lack dedicated tests.
- PostgreSQL backup/restore and off-site backup failures remain untested.
- The transaction service remains a large multi-concern module; no broad
refactor was attempted without evidence that it would reduce risk.
- The independent Round 1 adversarial recheck found a reversed purchase could
still be returned. This was fixed and covered in Round 2.
