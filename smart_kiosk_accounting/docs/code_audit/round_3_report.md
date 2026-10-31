# Round 3 — Reconciliation Query Optimization

## Changes

- Extracted `stock_totals_by_product` to provide a shared grouped aggregation
  for stock quantities and positive carrying values.
- Updated inventory reconciliation to aggregate all batch balances at once,
  rather than querying quantity and value for each product.
- Reused the aggregate in the inventory page, keeping its two-query behavior
  independent of product count.
- Updated customer/supplier reconciliation to aggregate posted transaction
  totals by party and transaction type rather than repeatedly scanning the
  transaction ledger for every party.
- Added query-count tests with multiple products and multiple parties.

## Verification

- Full suite: **56 tests passed** (`python -m unittest discover -s tests -q`).
- Query-count assertions: stock page **2 SELECTs**, inventory reconciliation
  **5 SELECTs**, party reconciliation **7 SELECTs** for the test datasets.
- `compileall`: passed.
- `git diff --check`: passed.
- `pip check`: passed, no broken requirements.
- Reconciliations remain covered by financial scenario and ledger consistency
  tests.

## Round 3 score

The independent Round 3 score was **86/100**, up from **84/100**. The scorer
credited the shared grouped inventory aggregate and bounded reconciliation
query counts. It noted that these query counts do not establish large-dataset
latency or PostgreSQL performance.

| Category | Score | Evidence and remaining gap |
|---|---:|---|
| Correctness and functionality | 17/20 | Reversal and return safeguards have regressions; unusual settlement histories remain. |
| Code quality and maintainability | 12/15 | Shared stock aggregation reduces duplication; transaction service remains large. |
| Security | 12/15 | Onboarding permissions verified; production deployment remains unaudited. |
| Database integrity | 9/10 | Tenant FK cases pass on synthetic SQLite; other backend behavior remains untested. |
| Accounting accuracy | 14/15 | Grouped formulas preserve party reconciliation and existing financial scenarios. |
| Performance | 9/10 | Fixed query counts verified; no large-data latency benchmark. |
| Testing and reliability | 9/10 | Reported 56 tests pass; coverage and static checks unavailable. |
| Documentation | 4/5 | README and audit reports capture workflows and limitations. |

## Remaining risks

- Query counts are regression evidence for small SQLite fixtures, not a
  production-scale benchmark.
- PostgreSQL execution and multi-process concurrency have not been tested.
- No coverage, static type, style, or dependency vulnerability scan is
  available in the active environment.
