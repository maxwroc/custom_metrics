# Project Guidelines

## Working Agreements
- Never commit (`git commit`) or push (`git push`) unless the user explicitly asks for it in that
  same turn. Making edits, running tests/lint, and other local changes do not imply permission to
  commit or push. A previous request to commit or push does NOT carry over to later changes — each
  commit/push needs its own explicit instruction.
- Whenever Python files were modified, check for Pylance errors on the changed file(s) before
  considering the implementation done, and fix any that are found.
- Whenever ONLY files under `custom_components/custom_metrics/www/` (the Lovelace card JS) were
  modified and no `.py` files changed, do NOT run the Python test suite (`pytest`) or ruff — they
  don't exercise or lint this JS at all (there's no build step, JS test suite, or JS linter in this
  repo; `tests/test_frontend.py` only checks that `frontend.py` registers the module URL, never
  the JS content). Just run `get_errors` on the changed file(s) instead. Only run pytest/ruff when
  a `.py` file was also changed in the same turn.
- When implementing a feature listed in `plan_sql.md`, update `plan_sql.md` to mark that feature as done
  (with a brief note on what changed) as part of finishing the implementation.
- Whenever a user-facing feature changes (e.g. card configuration options, services, WebSocket
  API), update `README.md` accordingly. README content must stay user-facing only (no
  implementation details) and as compact as possible - only what a user needs to use the feature.
