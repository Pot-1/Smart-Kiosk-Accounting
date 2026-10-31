# Final Quality Report

## Executive decision

**Release recommendation: not production-ready on the evidence available.**
Round 4 automated checks pass, and the reported security finding was confirmed
fixed by an independent security recheck. The current independent score is
**86/100**, below the requested 90/100 acceptance gate. The independent final
scorer recommends only a controlled SQLite pilot, not a high-confidence
production release. PostgreSQL, production load, static analysis, coverage,
and dependency vulnerability scanning were not verified.

The final independent engineering reviewer found no high-confidence
regression, recommended a conditional local SQLite release only, and offered
an informal estimate of about **84/100**. The formal weighted scorer's final
score is **86/100**.

## Before-and-after scores

| Evaluation point | Score |
|---|---:|
| Initial baseline | 74/100 |
| Round 1 | 82/100 |
| Round 2 | 84/100 |
| Round 3 | 86/100 |
| Round 4 final | 86/100 |

| Category | Final score | Maximum |
|---|---:|---:|
| Correctness and functionality | 17 | 20 |
| Code quality and maintainability | 12 | 15 |
| Security | 12 | 15 |
| Database integrity | 9 | 10 |
| Accounting accuracy | 14 | 15 |
| Performance | 9 | 10 |
| Testing and reliability | 9 | 10 |
| Documentation | 4 | 5 |
| **Total** | **86** | **100** |

The score is evidence-based and is not rounded up to satisfy the acceptance
threshold.

## Fixed findings

1. Lower-privilege users could alter opening balances and finish incomplete
   onboarding. `business_settings` authorization is checked at the start of
   `onboarding.render()`, before business lookup or mutation. A cashier
   permission test and an independent security recheck cover the correction.
2. Split-tender purchase returns could be posted entirely to cash/bank instead
   of partially reducing supplier payable. Returns now use the remaining
   credit allocation and outstanding supplier balance, and post the remainder
   to the selected refund account.
3. FIFO purchase returns could reduce a different receipt's value layer.
   Available valuation quantity from the selected original purchase item is
   preferred, with FIFO fallback when the selected layer is depleted.
4. Reversed purchases could be returned again. The service now accepts only
   posted purchases and tests that rejected returns do not change stock.
5. Inventory page and reconciliation operations issued per-product queries;
   party reconciliation issued per-party ledger scans. Shared/grouped
   aggregation now bounds query counts independently of product/party count.

## Code removed

No code was removed as dead or unnecessary. No removal was supported by
evidence during this audit.

## Tests added

- Cashier denial for business onboarding permissions.
- Split-tender supplier return, return after supplier settlement, and selected
  FIFO purchase-layer valuation.
- Rejection of returns against reversed purchases without stock changes.
- Cross-business reference rejection across transactions, journals, stock,
  payments, expenses, returns, audit, bank statement, receipts, and stock
  counts.
- Fixed query-count checks for product stock, inventory reconciliation, and
  party reconciliation.

## Final verification evidence

- Final full suite: **56 tests passed**.
- Independent adversarial full-suite run: **56 tests passed**.
- `compileall`, `git diff --check`, and `pip check`: passed.
- Disposable Streamlit application: HTTP 200 health response; sign-in page
  rendered.
- Disposable SQLite application database reached Alembic
  `0002_accounting_integrity`; expected stock-count tables were present.
- Disposable validation database and backup directory were removed.
- The ignored local business database and backups were not opened, read, or
  migrated; their names and file metadata were visible during status checks.

## Assessment by area

- **Security:** The discovered onboarding authorization weakness is fixed and
  independently rechecked. This does not constitute a complete deployment
  security certification. `pip-audit` was unavailable.
- **Accounting:** Supplier returns now distinguish payable reversals from
  cash refunds; FIFO returns target the selected source layer when available;
  reversed purchases cannot be returned. Scenario and reconciliation tests
  pass. Rounding and more complex settlement histories remain areas for more
  independent testing.
- **Database integrity:** Synthetic SQLite cross-business FK cases pass, and
  migration/startup reaches Alembic head. PostgreSQL constraints, backup and
  restore, and interrupted migrations were not exercised.
- **Performance:** Three hot paths now have bounded query counts. This is not a
  production latency benchmark; query execution on large datasets remains
  unmeasured.
- **Maintainability:** No broad refactor was justified. The transaction
  service remains large and would benefit from a separately scoped,
  regression-protected modularization effort.
- **Testing:** 56 tests pass. Ruff, mypy, pytest, and coverage were not
  available in the active environment.
- **Documentation:** README and all four round reports plus initial/final
  reports document changes and limitations.

## Unresolved issues and release gates

- The final score is 86/100, below the required 90/100 overall threshold.
- Static style/type/coverage checks and dependency vulnerability scan remain
  unverified because the tools are not installed.
- PostgreSQL and off-site backup/restore paths need safe integration testing.
- Production-scale performance, concurrent return/idempotency behavior, and
  additional rounding/period-end scenarios need test evidence.

Until those gates are addressed and independently validated, deploy only in a
controlled test environment with verified backups; do not describe the system
as production-ready.
