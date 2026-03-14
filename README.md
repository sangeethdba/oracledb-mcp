# OracleDB MCP Server

MCP server for Oracle SQL performance diagnostics and AWR analysis, compatible with any MCP client that supports `stdio` or HTTP transports.

## Tools Included

This server now includes a broad DBRE-focused Oracle toolset:

- health and readonly query execution
- SQL rewrite and bind-template generation
- AWR/ASH analysis and comparisons
- long-running query detection (OEM-style)
- bind-based SQL rewrite benchmark assistant (original vs rewritten with plan/timing diff)
- SQL plan regression detection and rescue playbooks
- SPM baseline create/manage/pack/unpack
- SQL Profile script generation (coe_xfr_sql_profile style)
- locking and blocking analysis
- privilege/schema/stats/index audits
- CPU/latency/memory/session pressure analytics
- child cursor explosion and bind sensitivity analysis
- alert log analyzer
- SQL patch quarantine helper
- SQL dependency impact map
- local SQL hotlist manager

Full per-tool catalog (signature, purpose, example call):
- `docs/TOOL_CATALOG.md` (auto-generated)
- Current tool count: **54**

## Setup

```bash
cd <repo_root>
python3 -m pip install -r requirements.txt
```

## Compatibility

- Oracle versions: designed for `11.2.0.4` through `23ai/23c`.
- RAC: tools use `GV$` views where cluster-wide context matters.
- SQL syntax compatibility: server now includes automatic fallback for `FETCH FIRST ... ROWS ONLY` to legacy `ROWNUM` pattern when running against older versions.
- Feature-dependent tools (AWR/ASH/SQL Monitor/SPM/SQL Patch/SQL Profile) require corresponding Oracle options, privileges, and pack licensing.

## Observability (OpenTelemetry)

MCP tool spans can be exported with OpenTelemetry.

Enable tracing:

```bash
export TRACING=enabled
export OTEL_TRACES_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318/v1/traces"
# optional:
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer <token>"
```

Console exporter (local debug):

```bash
export TRACING=enabled
export OTEL_TRACES_EXPORTER=console
```

### MCP context propagation

The server extracts trace context from MCP request `_meta` (when provided), including:

- `traceparent`
- `tracestate`
- `baggage`

This supports parent-child trace continuity between MCP clients and this server.

Reference:
- OpenTelemetry MCP semantic conventions (context propagation): `docs/gen-ai/mcp.md#context-propagation` in `open-telemetry/semantic-conventions`

## Run Server

### STDIO (best for most MCP clients)

```bash
python3 oracledb_mcp.py --transport stdio
```

### Streamable HTTP

```bash
python3 oracledb_mcp.py --transport streamable-http --port 8020
```

## Client Configuration Examples

## VS Code MCP (example)

```json
{
  "mcpServers": {
    "oracledb-mcp": {
      "command": "python3",
      "args": ["<repo_root>/oracledb_mcp.py", "--transport", "stdio"],
      "env": {
        "ORACLE_USER": "<db_username>",
        "ORACLE_PASSWORD": "<db_password>",
        "ORACLE_DSN": "host:1521/service"
      }
    }
  }
}
```

## Troubleshooting

- Symptom: MCP client shows `Running` then immediately `Stopped`.
- Fix checklist:
  - Ensure server entry uses stdio and points to `<repo_root>/oracledb_mcp.py`.
  - Ensure Python environment can import all requirements (`pip install -r requirements.txt`).
  - Confirm Oracle env vars are present: `ORACLE_USER`, `ORACLE_PASSWORD`, `ORACLE_DSN`.
  - Run directly to validate process startup:
    - `python3 <repo_root>/oracledb_mcp.py --transport stdio`
  - Run registration test:
    - `python3 -m pytest -q tests/test_oracledb_mcp.py`

## Cursor MCP (example)

```json
{
  "mcpServers": {
    "oracledb-mcp": {
      "command": "python3",
      "args": ["<repo_root>/oracledb_mcp.py", "--transport", "stdio"],
      "env": {
        "ORACLE_USER": "<db_username>",
        "ORACLE_PASSWORD": "<db_password>",
        "ORACLE_DSN": "host:1521/service"
      }
    }
  }
}
```

## Claude Desktop MCP (example)

```json
{
  "mcpServers": {
    "oracledb-mcp": {
      "command": "python3",
      "args": ["<repo_root>/oracledb_mcp.py", "--transport", "stdio"],
      "env": {
        "ORACLE_USER": "<db_username>",
        "ORACLE_PASSWORD": "<db_password>",
        "ORACLE_DSN": "host:1521/service"
      }
    }
  }
}
```

## Example Calls

- Query rewrite analysis:
  - `oracle_suggest_query_rewrite(sql_text="select * from orders where trunc(created_at)=:d", sql_id="8f6t6uk2y6fht")`
- Bind-template generation:
  - `oracle_generate_bind_query_from_vsql(sql_id="8f6t6uk2y6fht")`
- AWR analysis from file:
  - `oracle_analyze_awr_report(report_path="/tmp/awr_12345_12346.txt")`
- AWR comparison:
  - `oracle_compare_awr_reports(baseline_report_path="/tmp/awr_before.txt", target_report_path="/tmp/awr_after.txt")`
- Wait hotspot analysis:
  - `oracle_waits_hotspots(hours=2, top_n=20)`
- Blocking tree:
  - `oracle_blocking_sessions_analyzer(top_n=20)`
- Privilege audit:
  - `oracle_role_privilege_audit(username="TARGET_USER", include_object_privileges=true)`
- OEM-style long running SQL:
  - `oracle_oem_long_running_queries(min_elapsed_seconds=5, window_minutes=60, top_n=20, only_active=true)`
- Bind-based A/B SQL test:
  - `oracle_test_query_with_binds(original_sql="...", candidate_sql="...", bind_sets=[...], iterations=3, fetch_rows=200)`
- End-to-end rewrite advisor + benchmark:
  - `oracle_sql_rewrite_benchmark_assistant(sql_id="3mrzy6ugwvvz4", rewritten_sql="select ...", use_captured_binds=true, iterations=3, fetch_rows=200)`

## OEM-Style Tuning Workflow

1. Find active long-running SQL (for example >5s):
   - `oracle_oem_long_running_queries(min_elapsed_seconds=5, window_minutes=60, top_n=20, only_active=true)`
2. Deep diagnostics on selected SQL_ID:
   - `oracle_planx_sql_id(sql_id="...", lookback_days=7)`
3. Benchmark original vs rewritten SQL with bind sets:
   - `oracle_sql_rewrite_benchmark_assistant(sql_id="...", rewritten_sql="...", use_captured_binds=true, iterations=3)`
4. If rewritten SQL is consistently better and correct, roll out app change.

## Examples Folder

`examples/` now contains LLM-formatted prompt/response documentation (not raw JSON):

- `examples/TOOL_EXAMPLES.md`

## Testing

Registration test:
```bash
cd <repo_root>
python3 -m pytest -q tests/test_oracledb_mcp.py
```

Live Oracle integration test (executes all tools):
```bash
cd <repo_root>
ORACLE_USER='<db_username>' ORACLE_PASSWORD='<db_password>' ORACLE_DSN='<host:port/service>' python3 tests/integration_oracledb_mcp.py
```

## Open Source

- Contribution guide: `CONTRIBUTING.md`
- CI pipeline: `.github/workflows/ci.yml`
- Catalog generator: `scripts/generate_tool_catalog.py`

## Privileges Recommended

At minimum, grant access to required views/packages for your operational model:

- `V_$SQL`
- `V_$SQLAREA`
- `V_$SQL_BIND_CAPTURE`
- `V_$DATABASE`
- `V_$INSTANCE`
- `DBMS_WORKLOAD_REPOSITORY`

## Notes

- `oracle_execute_readonly_query` intentionally blocks non-`SELECT`/`WITH` SQL for safety.
- Bind capture visibility depends on Oracle version, cursor lifecycle, and capture settings.
- AWR tooling works best with text exports. HTML can still work but parsed detail may be reduced.
