# Round 4 — Final Verification

## Verification results

- Full unittest suite: **56 tests passed** in the final run.
- Independent adversarial run: **56 tests passed**.
- `compileall`: passed.
- `git diff --check`: passed.
- `pip check`: passed.
- Disposable Streamlit app: HTTP health endpoint returned **200** and the
  sign-in page rendered.
- Disposable SQLite schema reached Alembic revision
  `0002_accounting_integrity`; stock-count tables were present.
- The disposable app database and backup directory were removed after
  verification. The local ignored business database and its backups were not
  opened, read, or migrated.
- The security reviewer independently confirmed the onboarding permission
  check blocks lower-privilege users from setup writes and completion.

## Round 4 score

The independent final score is **86/100**, unchanged from Round 3 and below
the requested **90/100** acceptance gate. The final scorer supports a
controlled SQLite pilot, not a high-confidence production release. The
independent engineering reviewer found no high-confidence defect or
regression, gave a non-formal estimate of about **84/100**, and recommended a
conditional local SQLite release only.

| Category | Score | Maximum |
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

## Release status

Known validation gaps include unavailable Ruff, mypy, pytest, coverage, and
pip-audit; no PostgreSQL or managed/off-site backup test; no concurrent
return/idempotency or authentication-expiry boundary test; and no production
load benchmark. The score is reported as-is and is not rounded up or
represented as generally production-ready.
