# Contributing

Thanks for contributing to `oracledb-mcp`.

## Project Scope

This repository is MCP-server focused.

- Keep changes centered on `oracledb_mcp.py`, tests, and MCP-facing docs.
- Avoid adding local demo environments or environment-specific scripts.
- Preserve read-only safety for query execution tools.

## Local Setup

```bash
cd /Users/SXT6582/oracledb-mcp
python3 -m pip install -r requirements.txt
```

Optional live integration env vars:

```bash
export ORACLE_USER='<db_username>'
export ORACLE_PASSWORD='<db_password>'
export ORACLE_DSN='<host:port/service>'
```

## Required Checks Before PR

Run all of these locally:

```bash
python3 -m py_compile oracledb_mcp.py shared_utils.py scripts/generate_tool_catalog.py
python3 -m pytest -q tests/test_oracledb_mcp.py
python3 scripts/generate_tool_catalog.py
git diff -- docs/TOOL_CATALOG.md
```

If Oracle access is available, also run:

```bash
python3 tests/integration_oracledb_mcp.py
```

## Tool Changes

When adding or changing a tool:

1. Add/update tool implementation in `oracledb_mcp.py`.
2. Add/adjust tests in `tests/`.
3. Regenerate `docs/TOOL_CATALOG.md`.
4. Update user-facing docs in `README.md` or `examples/` when behavior changes.

## Compatibility Requirements

- Target support range: Oracle `11.2.0.4` through `23ai/23c`.
- Prefer `GV$` views when cluster-wide/RAC context is needed.
- Maintain compatibility fallback patterns where older syntax is unsupported.

## Security and Safety

- Do not commit credentials, wallets, or `.env` files.
- Use placeholders in docs (`<db_username>`, `<db_password>`).
- Keep dangerous operations behind explicit confirmation parameters.

## Pull Request Guidance

- Keep PRs focused and small where possible.
- Include a short summary:
  - what changed
  - why it changed
  - what tests you ran
- If behavior changed, include example input/output in the PR description.
