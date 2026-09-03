# Project Guidelines

## Working Agreements
- Never commit (`git commit`) or push (`git push`) unless the user explicitly asks for it in that
  same turn. Making edits, running tests/lint, and other local changes do not imply permission to
  commit or push. A previous request to commit or push does NOT carry over to later changes — each
  commit/push needs its own explicit instruction.
- Whenever Python files were modified, check for Pylance errors on the changed file(s) before
  considering the implementation done, and fix any that are found.
- When implementing a feature listed in `plan_sql.md`, update `plan_sql.md` to mark that feature as done
  (with a brief note on what changed) as part of finishing the implementation.
- Whenever a user-facing feature changes (e.g. card configuration options, services, WebSocket
  API), update `README.md` accordingly. README content must stay user-facing only (no
  implementation details) and as compact as possible - only what a user needs to use the feature.
