#!/usr/bin/env python3
"""
OracleDB MCP Server

MCP tools for Oracle performance troubleshooting and SQL tuning:
- Execute read-only SQL
- Suggest query rewrites
- Generate bind-variable query templates from V$ views
- Analyze AWR reports
- Compare two AWR reports
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from fastmcp import FastMCP

try:
    import oracledb  # type: ignore
except Exception:
    oracledb = None

sys.path.append('/home/claude')
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared_utils import JSONFormatter, get_logger
from shared_utils import initialize_tracing, trace_tool


load_dotenv()

parser = argparse.ArgumentParser(description="OracleDB MCP Server")
parser.add_argument(
    "--transport",
    choices=["stdio", "sse", "streamable-http"],
    default="stdio",
    help="Transport protocol",
)
parser.add_argument("--port", type=int, default=8020, help="Port for SSE/HTTP transports")
args, unknown = parser.parse_known_args()

initialize_tracing("oracledb-mcp", service_version=os.getenv("VERSION", "dev"))

mcp = FastMCP("oracledb-mcp")
logger = get_logger("oracledb-mcp")


@dataclass
class OracleConfig:
    user: str
    password: str
    dsn: str
    wallet_location: Optional[str] = None
    config_dir: Optional[str] = None


def _oracle_config() -> OracleConfig:
    user = os.getenv("ORACLE_USER", "")
    password = os.getenv("ORACLE_PASSWORD", "")
    dsn = os.getenv("ORACLE_DSN", "")

    if not user or not password or not dsn:
        raise ValueError(
            "Missing Oracle configuration. Set ORACLE_USER, ORACLE_PASSWORD, and ORACLE_DSN."
        )

    return OracleConfig(
        user=user,
        password=password,
        dsn=dsn,
        wallet_location=os.getenv("ORACLE_WALLET_LOCATION"),
        config_dir=os.getenv("ORACLE_CONFIG_DIR"),
    )


def _connect():
    if oracledb is None:
        raise RuntimeError(
            "python-oracledb is not installed. Install with: pip install oracledb"
        )

    cfg = _oracle_config()
    connect_kwargs: Dict[str, Any] = {
        "user": cfg.user,
        "password": cfg.password,
        "dsn": cfg.dsn,
    }

    if cfg.config_dir:
        connect_kwargs["config_dir"] = cfg.config_dir
    if cfg.wallet_location:
        connect_kwargs["wallet_location"] = cfg.wallet_location

    return oracledb.connect(**connect_kwargs)


def _root_service_dsn(dsn: str) -> str:
    if "/" not in dsn:
        return dsn
    base, _svc = dsn.rsplit("/", 1)
    return f"{base}/XE"


def _ensure_read_only_sql(sql: str) -> None:
    statement = sql.strip().lower()
    if statement.startswith("select") or statement.startswith("with"):
        return
    raise ValueError("Only read-only SELECT/WITH statements are allowed in oracle_execute_readonly_query")


def _rewrite_fetch_first_for_legacy(sql: str) -> str:
    s = sql.strip().rstrip(";")
    m = re.search(
        r"(?is)^(.*?)(?:\s+fetch\s+first\s+(:[A-Za-z_][A-Za-z0-9_]*|\d+)\s+rows?\s+only)\s*$",
        s,
    )
    if not m:
        return sql
    core = m.group(1).strip()
    limit_token = m.group(2).strip()
    return f"select * from (\n{core}\n) where rownum <= {limit_token}"


def _execute_compat(cursor, sql: str, binds: Optional[Any] = None):
    try:
        return cursor.execute(sql, binds or {})
    except Exception as e:
        msg = str(e)
        # 11g compatibility fallback: FETCH FIRST is unsupported pre-12c.
        if "ORA-00933" in msg or "ORA-00923" in msg or "ORA-00905" in msg:
            legacy_sql = _rewrite_fetch_first_for_legacy(sql)
            if legacy_sql != sql:
                return cursor.execute(legacy_sql, binds or {})
        raise


def _rows_to_dicts(cursor, max_rows: int) -> Tuple[List[Dict[str, Any]], bool]:
    columns = [d[0].lower() for d in cursor.description]
    rows: List[Dict[str, Any]] = []
    truncated = False

    for i, row in enumerate(cursor):
        if i >= max_rows:
            truncated = True
            break
        safe_row: Dict[str, Any] = {}
        for idx, value in enumerate(row):
            if isinstance(value, (datetime, date)):
                safe_row[columns[idx]] = value.isoformat()
            else:
                safe_row[columns[idx]] = value
        rows.append(safe_row)

    return rows, truncated


def _normalize_sql(sql: str) -> str:
    # Keep normalization simple and deterministic for matching SQL text.
    return re.sub(r"\s+", " ", sql.strip()).upper()


def _q_quote(text: str) -> str:
    candidates = ["[", "{", "<", "(", "|", "~", "^", "@", "#", "%", "$", "!"]
    closer = {"[": "]", "{": "}", "<": ">", "(": ")"}
    for c in candidates:
        end = closer.get(c, c)
        if c not in text and end not in text:
            return f"q'{c}{text}{end}'"
    return "'" + text.replace("'", "''") + "'"


def _chunk_text(value: str, chunk_size: int = 500) -> List[str]:
    if not value:
        return []
    out: List[str] = []
    i = 0
    n = len(value)
    while i < n:
        out.append(value[i : i + chunk_size])
        i += chunk_size
    return out


def _format_cursor_address(address: Any) -> str:
    if isinstance(address, (bytes, bytearray)):
        return address.hex().upper()
    return str(address)


def _hotlist_path() -> Path:
    custom = os.getenv("ORACLE_MCP_HOTLIST_FILE")
    if custom:
        return Path(custom)
    return Path.home() / ".oracledb_mcp_hotlist.json"


def _load_hotlist() -> Dict[str, Any]:
    p = _hotlist_path()
    if not p.exists():
        return {"items": []}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"items": []}


def _save_hotlist(data: Dict[str, Any]) -> None:
    p = _hotlist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, default=str))


def _validate_sql_id(sql_id: str) -> str:
    s = (sql_id or "").strip().lower()
    if not re.fullmatch(r"[0-9a-z]{13}", s):
        raise ValueError("sql_id must be a 13-character lowercase Oracle SQL_ID.")
    return s


def _heuristic_sql_rewrite_suggestions(sql: str) -> List[Dict[str, str]]:
    src = _normalize_sql(sql)
    suggestions: List[Dict[str, str]] = []

    if "SELECT *" in src:
        suggestions.append({
            "issue": "Uses SELECT *",
            "recommendation": "Project only required columns to reduce I/O, CPU, and network transfer.",
        })

    if " OR " in src and " WHERE " in src:
        suggestions.append({
            "issue": "OR predicates in WHERE clause",
            "recommendation": "Consider UNION ALL branches (when semantically safe) to improve index usage.",
        })

    if re.search(r"WHERE\s+.*\bTO_CHAR\(|WHERE\s+.*\bTO_DATE\(|WHERE\s+.*\bTRUNC\(", src):
        suggestions.append({
            "issue": "Function applied to filtered column",
            "recommendation": "Avoid wrapping indexed columns in functions; transform constants or use function-based indexes.",
        })

    if re.search(r"\bNOT\s+IN\s*\(", src):
        suggestions.append({
            "issue": "NOT IN subquery/values",
            "recommendation": "Prefer NOT EXISTS for NULL-safe semantics and often better plans.",
        })

    if re.search(r"\bIN\s*\(\s*SELECT", src):
        suggestions.append({
            "issue": "IN (SELECT ...) pattern",
            "recommendation": "Evaluate EXISTS or SEMI-JOIN-friendly rewrites depending on cardinality.",
        })

    if re.search(r"\bDISTINCT\b", src) and re.search(r"\bGROUP\s+BY\b", src):
        suggestions.append({
            "issue": "DISTINCT and GROUP BY both used",
            "recommendation": "Check if DISTINCT is redundant when GROUP BY already guarantees uniqueness.",
        })

    if not suggestions:
        suggestions.append({
            "issue": "No obvious anti-pattern detected",
            "recommendation": "Review execution plan, row estimates, and access paths to tune joins and indexes.",
        })

    return suggestions


def _parse_awr_metrics(content: str) -> Dict[str, Any]:
    # Lightweight parser that works on plain text exports and copied snippets.
    metrics: Dict[str, Any] = {
        "db_time_s": None,
        "db_cpu_s": None,
        "elapsed_min": None,
        "aas": None,
        "top_wait_events": [],
        "top_sql_ids": [],
    }

    def num(x: str) -> Optional[float]:
        x = x.replace(",", "").strip()
        try:
            return float(x)
        except Exception:
            return None

    elapsed = re.search(r"Elapsed:\s*(\d+(?:\.\d+)?)\s*\(mins?\)", content, re.IGNORECASE)
    if elapsed:
        metrics["elapsed_min"] = num(elapsed.group(1))

    db_time = re.search(r"DB Time\s*[:|]\s*([0-9,]+(?:\.[0-9]+)?)", content, re.IGNORECASE)
    if db_time:
        metrics["db_time_s"] = num(db_time.group(1))

    db_cpu = re.search(r"DB CPU\s*[:|]\s*([0-9,]+(?:\.[0-9]+)?)", content, re.IGNORECASE)
    if db_cpu:
        metrics["db_cpu_s"] = num(db_cpu.group(1))

    aas = re.search(r"Average Active Sessions\s*[:|]\s*([0-9,]+(?:\.[0-9]+)?)", content, re.IGNORECASE)
    if aas:
        metrics["aas"] = num(aas.group(1))

    # Parse Top Foreground Wait Events style rows.
    for line in content.splitlines():
        # Example often includes event name then waits/timeouts/avg wait/%DB time.
        m = re.match(r"\s*([A-Za-z0-9_\- /\.\(\)]+?)\s{2,}([0-9,]+)\s+([0-9,]+(?:\.[0-9]+)?)", line)
        if not m:
            continue
        event = m.group(1).strip()
        waits = num(m.group(2))
        time_s = num(m.group(3))
        if event and waits is not None and time_s is not None:
            metrics["top_wait_events"].append(
                {"event": event, "waits": waits, "time_s": time_s}
            )
            if len(metrics["top_wait_events"]) >= 10:
                break

    # Parse SQL ID mentions in top SQL sections.
    for match in re.finditer(r"\b([0-9a-z]{13})\b", content):
        sid = match.group(1)
        if sid not in metrics["top_sql_ids"]:
            metrics["top_sql_ids"].append(sid)
        if len(metrics["top_sql_ids"]) >= 15:
            break

    return metrics


def _compare_metric(before: Optional[float], after: Optional[float]) -> Dict[str, Any]:
    if before is None or after is None:
        return {"before": before, "after": after, "delta": None, "pct_change": None}
    delta = after - before
    pct = (delta / before * 100.0) if before != 0 else None
    return {
        "before": before,
        "after": after,
        "delta": round(delta, 2),
        "pct_change": round(pct, 2) if pct is not None else None,
    }


def _read_report_input(report_text: Optional[str], report_path: Optional[str]) -> str:
    if report_text and report_text.strip():
        return report_text
    if report_path and report_path.strip():
        path = Path(report_path).expanduser()
        if not path.exists():
            raise ValueError(f"AWR report path does not exist: {path}")
        return path.read_text(encoding="utf-8", errors="ignore")
    raise ValueError("Provide either report_text or report_path")


def _exec_query(cur, sql: str, binds: Optional[Dict[str, Any]] = None) -> Tuple[List[str], List[Tuple[Any, ...]]]:
    _execute_compat(cur, sql, binds or {})
    if cur.description is None:
        return [], []
    cols = [d[0].lower() for d in cur.description]
    rows = cur.fetchall()
    return cols, rows


def _rows_dict(cols: List[str], rows: List[Tuple[Any, ...]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        item: Dict[str, Any] = {}
        for i in range(len(cols)):
            value = row[i]
            if isinstance(value, (datetime, date)):
                item[cols[i]] = value.isoformat()
            else:
                item[cols[i]] = value
        out.append(item)
    return out


def _try_queries(cur, queries: List[Tuple[str, Optional[Dict[str, Any]]]]) -> Tuple[List[str], List[Tuple[Any, ...]], str]:
    last_error = ""
    for sql, binds in queries:
        try:
            cols, rows = _exec_query(cur, sql, binds)
            return cols, rows, sql
        except Exception as e:
            last_error = str(e)
            continue
    raise RuntimeError(f"all query variants failed: {last_error}")


@mcp.tool()
@trace_tool
async def oracle_health_check() -> str:
    """Validate Oracle DB connectivity and return database identity details."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _execute_compat(cur, 
                """
                select
                    sys_context('USERENV', 'DB_NAME') as db_name,
                    sys_context('USERENV', 'INSTANCE_NAME') as instance_name,
                    sys_context('USERENV', 'SERVICE_NAME') as service_name,
                    sys_context('USERENV', 'SESSION_USER') as session_user
                from dual
                """
            )
            row = cur.fetchone()
            return JSONFormatter.format_response(
                {
                    "status": "ok",
                    "db_name": row[0],
                    "instance_name": row[1],
                    "service_name": row[2],
                    "session_user": row[3],
                }
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_create_awr_snapshot() -> str:
    """
    Create AWR snapshot and return latest snapshot ID.
    Equivalent to awr_snapshot.sql.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.callproc("dbms_workload_repository.create_snapshot")
            _execute_compat(cur, 
                """
                select snap_id, dbid, instance_number, begin_interval_time, end_interval_time
                from (
                    select snap_id, dbid, instance_number, begin_interval_time, end_interval_time
                    from dba_hist_snapshot
                    order by snap_id desc
                )
                fetch first 1 rows only
                """
            )
            row = cur.fetchone()
            return JSONFormatter.format_response(
                {
                    "status": "created",
                    "snapshot": {
                        "snap_id": int(row[0]) if row else None,
                        "dbid": int(row[1]) if row else None,
                        "instance_number": int(row[2]) if row else None,
                        "begin_interval_time": row[3].isoformat() if row and row[3] else None,
                        "end_interval_time": row[4].isoformat() if row and row[4] else None,
                    },
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_planx_sql_id(
    sql_id: str,
    lookback_days: int = 14,
    include_plan_text: bool = True,
    plan_format: str = "ALLSTATS LAST +PEEKED_BINDS +OUTLINE",
) -> str:
    """
    PlanX-style SQL_ID diagnostics: SQL text, plan performance (memory/AWR), binds, ASH waits, and optional DBMS_XPLAN output.
    Inspired by cs_planx.sql.
    """
    sql_id = _validate_sql_id(sql_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _execute_compat(cur, 
                """
                select sql_id, plan_hash_value, child_number, parsing_schema_name, module, executions,
                       round(elapsed_time/nullif(executions,0)/1000000,6) elapsed_s_per_exec,
                       round(cpu_time/nullif(executions,0)/1000000,6) cpu_s_per_exec,
                       round(buffer_gets/nullif(executions,0),2) lio_per_exec,
                       to_char(last_active_time, 'YYYY-MM-DD HH24:MI:SS') last_active_time
                from gv$sql
                where sql_id = :sql_id
                order by last_active_time desc
                fetch first 20 rows only
                """,
                {"sql_id": sql_id},
            )
            mem_rows = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())

            _execute_compat(cur, 
                """
                select h.plan_hash_value,
                       sum(h.executions_delta) execs,
                       round(sum(h.elapsed_time_delta)/nullif(sum(h.executions_delta),0)/1000000,6) elapsed_s_per_exec,
                       round(sum(h.cpu_time_delta)/nullif(sum(h.executions_delta),0)/1000000,6) cpu_s_per_exec,
                       round(sum(h.buffer_gets_delta)/nullif(sum(h.executions_delta),0),2) lio_per_exec
                from dba_hist_sqlstat h
                join dba_hist_snapshot sn
                  on sn.snap_id = h.snap_id
                 and sn.dbid = h.dbid
                 and sn.instance_number = h.instance_number
                where h.sql_id = :sql_id
                  and sn.begin_interval_time >= systimestamp - numtodsinterval(:days, 'DAY')
                  and h.executions_delta > 0
                group by h.plan_hash_value
                order by elapsed_s_per_exec nulls last
                """,
                {"sql_id": sql_id, "days": max(1, lookback_days)},
            )
            awr_rows = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())

            _execute_compat(cur, 
                """
                select sql_fulltext
                from v$sql
                where sql_id = :sql_id
                order by last_active_time desc
                fetch first 1 row only
                """,
                {"sql_id": sql_id},
            )
            sql_text_row = cur.fetchone()
            sql_text = str(sql_text_row[0]) if sql_text_row else None

            bind_rows: List[Dict[str, Any]] = []
            try:
                _execute_compat(cur, 
                    """
                    select name, position, datatype_string, value_string,
                           to_char(last_captured, 'YYYY-MM-DD HH24:MI:SS') last_captured
                    from v$sql_bind_capture
                    where sql_id = :sql_id
                    order by position
                    """,
                    {"sql_id": sql_id},
                )
                bind_rows = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            except Exception:
                bind_rows = []

            _execute_compat(cur, 
                """
                select wait_class, event, count(*) samples
                from v$active_session_history
                where sql_id = :sql_id
                  and sample_time >= systimestamp - numtodsinterval(:days, 'DAY')
                group by wait_class, event
                order by samples desc
                fetch first 15 rows only
                """,
                {"sql_id": sql_id, "days": max(1, lookback_days)},
            )
            ash_waits = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())

            plan_text_lines: List[str] = []
            if include_plan_text and mem_rows:
                child = mem_rows[0].get("child_number")
                try:
                    _execute_compat(cur, 
                        """
                        select plan_table_output
                        from table(dbms_xplan.display_cursor(:sql_id, :child_no, :fmt))
                        """,
                        {"sql_id": sql_id, "child_no": child, "fmt": plan_format},
                    )
                    plan_text_lines = [str(r[0]) for r in cur.fetchall()]
                except Exception:
                    plan_text_lines = []

            return JSONFormatter.format_response(
                {
                    "sql_id": sql_id,
                    "lookback_days": max(1, lookback_days),
                    "sql_text": sql_text,
                    "memory_plan_metrics": mem_rows,
                    "awr_plan_metrics": awr_rows,
                    "bind_capture": bind_rows,
                    "ash_waits": ash_waits,
                    "dbms_xplan_display_cursor": "\n".join(plan_text_lines) if plan_text_lines else None,
                    "notes": [
                        "This consolidates core cs_planx-style diagnostics for one SQL_ID.",
                        "Use alongside oracle_sql_plan_regression_detector and oracle_sql_plan_rescue_playbook for remediation.",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_purge_cursor_by_sql_id(
    sql_id: str,
    confirm_apply: bool = False,
) -> str:
    """
    Purge cursor(s) for a SQL_ID using DBMS_SHARED_POOL.PURGE.
    Inspired by cs_purge_cursor.sql. Dry-run unless confirm_apply=true.
    """
    sql_id = _validate_sql_id(sql_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _execute_compat(cur, 
                """
                select inst_id, address, hash_value, plan_hash_value, executions
                from gv$sqlarea
                where sql_id = :sql_id
                order by inst_id
                """,
                {"sql_id": sql_id},
            )
            rows = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            purge_targets = []
            for r in rows:
                addr = r.get("address")
                hv = r.get("hash_value")
                if addr and hv is not None:
                    addr_txt = _format_cursor_address(addr)
                    purge_targets.append(
                        {
                            "inst_id": r.get("inst_id"),
                            "plan_hash_value": r.get("plan_hash_value"),
                            "executions": r.get("executions"),
                            "purge_name": f"{addr_txt},{hv}",
                            "purge_call": f"exec dbms_shared_pool.purge('{addr_txt},{hv}', 'C');",
                        }
                    )

            if not confirm_apply:
                return JSONFormatter.format_response(
                    {
                        "sql_id": sql_id,
                        "dry_run": True,
                        "purge_targets": purge_targets,
                        "next_step": "Set confirm_apply=true to execute DBMS_SHARED_POOL.PURGE for listed cursors.",
                    },
                    optimize=True,
                )

            results = []
            for t in purge_targets:
                status = "ok"
                error = None
                try:
                    cur.callproc("dbms_shared_pool.purge", [t["purge_name"], "C"])
                except Exception as e:
                    status = "failed"
                    error = str(e)
                results.append(
                    {
                        "purge_name": t["purge_name"],
                        "status": status,
                        "error": error,
                    }
                )

            conn.commit()
            return JSONFormatter.format_response(
                {
                    "sql_id": sql_id,
                    "dry_run": False,
                    "results": results,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_execute_readonly_query(
    sql: str,
    binds: Optional[Dict[str, Any]] = None,
    max_rows: int = 200,
) -> str:
    """Execute a read-only SQL query (SELECT/WITH only) with optional bind values."""
    _ensure_read_only_sql(sql)

    conn = _connect()
    try:
        with conn.cursor() as cur:
            _execute_compat(cur, sql, binds or {})
            if cur.description is None:
                return JSONFormatter.format_response({"rows": [], "count": 0})

            rows, truncated = _rows_to_dicts(cur, max_rows=max_rows)
            return JSONFormatter.format_response(
                {
                    "count": len(rows),
                    "truncated": truncated,
                    "max_rows": max_rows,
                    "rows": rows,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_suggest_query_rewrite(
    sql_text: str,
    sql_id: Optional[str] = None,
) -> str:
    """
    Suggest SQL rewrite opportunities for a custom query.
    Optionally enrich with cursor-level runtime stats from V$SQL using sql_id.
    """
    suggestions = _heuristic_sql_rewrite_suggestions(sql_text)
    runtime_context: Dict[str, Any] = {}

    if sql_id:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                _execute_compat(cur, 
                    """
                    select
                        sql_id,
                        plan_hash_value,
                        parsing_schema_name,
                        executions,
                        elapsed_time,
                        cpu_time,
                        buffer_gets,
                        disk_reads,
                        rows_processed,
                        fetches
                    from v$sql
                    where sql_id = :sql_id
                    order by last_active_time desc
                    fetch first 1 row only
                    """,
                    {"sql_id": sql_id},
                )
                row = cur.fetchone()
                if row:
                    runtime_context = {
                        "sql_id": row[0],
                        "plan_hash_value": row[1],
                        "parsing_schema_name": row[2],
                        "executions": row[3],
                        "elapsed_time_micro": row[4],
                        "cpu_time_micro": row[5],
                        "buffer_gets": row[6],
                        "disk_reads": row[7],
                        "rows_processed": row[8],
                        "fetches": row[9],
                    }
        finally:
            conn.close()

    return JSONFormatter.format_response(
        {
            "input_sql": sql_text,
            "runtime_context": runtime_context,
            "rewrite_suggestions": suggestions,
            "next_steps": [
                "Capture DBMS_XPLAN.DISPLAY_CURSOR(sql_id => ..., format => 'ALLSTATS LAST').",
                "Check cardinality estimates and join order against actual row counts.",
                "Validate index selectivity and stale stats before enforcing hints.",
            ],
        }
    )


@mcp.tool()
@trace_tool
async def oracle_generate_bind_query_from_vsql(
    sql_id: Optional[str] = None,
    sql_text: Optional[str] = None,
    include_capture_values: bool = True,
) -> str:
    """
    Generate a bind-variable SQL template by querying V$SQL and V$SQL_BIND_CAPTURE.

    Provide either sql_id directly, or sql_text for lookup.
    """
    if not sql_id and not sql_text:
        raise ValueError("Provide either sql_id or sql_text")

    conn = _connect()
    try:
        with conn.cursor() as cur:
            resolved_sql_id = sql_id
            if not resolved_sql_id and sql_text:
                normalized = _normalize_sql(sql_text)
                _execute_compat(cur, 
                    """
                    select sql_id, sql_text
                    from v$sqlarea
                    where upper(regexp_replace(sql_text, '\\s+', ' ')) = :normalized
                    order by last_active_time desc
                    fetch first 1 row only
                    """,
                    {"normalized": normalized},
                )
                row = cur.fetchone()
                if row:
                    resolved_sql_id = row[0]
                else:
                    raise ValueError("No matching SQL found in V$SQLAREA for provided sql_text")

            _execute_compat(cur, 
                """
                select sql_fulltext
                from v$sql
                where sql_id = :sql_id
                order by last_active_time desc
                fetch first 1 row only
                """,
                {"sql_id": resolved_sql_id},
            )
            sql_row = cur.fetchone()
            if not sql_row:
                raise ValueError(f"SQL_ID not found in V$SQL: {resolved_sql_id}")

            original_sql = str(sql_row[0])

            _execute_compat(cur, 
                """
                select
                    name,
                    position,
                    datatype_string,
                    value_string,
                    last_captured
                from v$sql_bind_capture
                where sql_id = :sql_id
                order by position
                """,
                {"sql_id": resolved_sql_id},
            )
            bind_rows = cur.fetchall()

            binds: List[Dict[str, Any]] = []
            for row in bind_rows:
                bind_info = {
                    "name": row[0],
                    "position": row[1],
                    "datatype": row[2],
                    "last_captured": str(row[4]) if row[4] is not None else None,
                }
                if include_capture_values:
                    bind_info["captured_value"] = row[3]
                binds.append(bind_info)

            template_sql = original_sql
            for b in binds:
                if b.get("name"):
                    raw = b["name"]
                    named = raw if str(raw).startswith(":") else f":{raw}"
                    positional = f":{b['position']}"
                    template_sql = re.sub(rf"(?<!\w){re.escape(positional)}(?!\w)", named, template_sql)

            bind_example = {
                (b["name"].lstrip(":") if b.get("name") else f"b{b['position']}"): (
                    b.get("captured_value") if include_capture_values else None
                )
                for b in binds
            }

            return JSONFormatter.format_response(
                {
                    "sql_id": resolved_sql_id,
                    "bind_count": len(binds),
                    "binds": binds,
                    "template_sql": template_sql,
                    "example_bind_map": bind_example,
                    "recommendations": [
                        "Use bind variables for literals to reduce hard parsing and shared pool churn.",
                        "Check V$SQL_SHARED_CURSOR if many child cursors exist for the same SQL_ID.",
                        "For skewed predicates, validate bind peeking/adaptive cursor sharing behavior.",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_generate_sql_profile_script(
    sql_id: str,
    plan_hash_value: int,
    profile_name: Optional[str] = None,
    force_match: bool = False,
    category: str = "DEFAULT",
) -> str:
    """
    Generate a SQL script to create a manual SQL Profile from known plan outline hints,
    similar to coe_xfr_sql_profile.sql workflow.
    """
    sql_id = _validate_sql_id(sql_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            plan_list_sql = """
                with p as (
                    select plan_hash_value
                    from gv$sql_plan
                    where sql_id = :sql_id and other_xml is not null
                    union
                    select plan_hash_value
                    from dba_hist_sql_plan
                    where sql_id = :sql_id
                      and dbid = (select dbid from v$database)
                      and other_xml is not null
                ),
                m as (
                    select plan_hash_value,
                           sum(elapsed_time) / nullif(sum(executions), 0) avg_et_us,
                           sum(cpu_time) / nullif(sum(executions), 0) avg_cpu_us,
                           sum(buffer_gets) / nullif(sum(executions), 0) lio_per_exec,
                           sum(executions) executions
                    from gv$sql
                    where sql_id = :sql_id
                      and executions > 0
                    group by plan_hash_value
                ),
                a as (
                    select plan_hash_value,
                           sum(elapsed_time_delta) / nullif(sum(executions_delta), 0) avg_et_us,
                           sum(cpu_time_delta) / nullif(sum(executions_delta), 0) avg_cpu_us,
                           sum(buffer_gets_delta) / nullif(sum(executions_delta), 0) lio_per_exec,
                           sum(executions_delta) executions
                    from dba_hist_sqlstat
                    where sql_id = :sql_id
                      and executions_delta > 0
                    group by plan_hash_value
                )
                select
                    p.plan_hash_value,
                    round(m.avg_et_us / 1000, 3) mem_elapsed_ms_per_exec,
                    round(a.avg_et_us / 1000, 3) awr_elapsed_ms_per_exec,
                    round(m.avg_cpu_us / 1000, 3) mem_cpu_ms_per_exec,
                    round(a.avg_cpu_us / 1000, 3) awr_cpu_ms_per_exec,
                    round(m.lio_per_exec, 2) mem_lio_per_exec,
                    round(a.lio_per_exec, 2) awr_lio_per_exec,
                    m.executions mem_execs,
                    a.executions awr_execs
                from p
                left join m on m.plan_hash_value = p.plan_hash_value
                left join a on a.plan_hash_value = p.plan_hash_value
                order by nvl(m.avg_et_us, a.avg_et_us) nulls last
            """
            pcols, prows = _exec_query(cur, plan_list_sql, {"sql_id": sql_id})
            available_plans = _rows_dict(pcols, prows)

            sql_text = None
            txt_sql = """
                select sql_fulltext
                from v$sqlarea
                where sql_id = :sql_id
                  and sql_fulltext is not null
                  and rownum = 1
            """
            try:
                tcols, trows = _exec_query(cur, txt_sql, {"sql_id": sql_id})
                if trows:
                    sql_text = _rows_dict(tcols, trows)[0].get("sql_fulltext")
            except Exception:
                sql_text = None
            if not sql_text:
                awr_txt_sql = """
                    select sql_text
                    from dba_hist_sqltext
                    where sql_id = :sql_id
                      and sql_text is not null
                      and rownum = 1
                """
                atcols, atrows = _exec_query(cur, awr_txt_sql, {"sql_id": sql_id})
                if atrows:
                    sql_text = _rows_dict(atcols, atrows)[0].get("sql_text")
            if not sql_text:
                return JSONFormatter.format_response(
                    {
                        "error": "SQL text not found in memory or AWR for provided SQL_ID.",
                        "sql_id": sql_id,
                    }
                )

            other_xml = None
            retrieval_paths = [
                (
                    """
                    select other_xml
                    from gv$sql_plan
                    where sql_id = :sql_id
                      and plan_hash_value = :phv
                      and other_xml is not null
                    order by child_number, id
                    fetch first 1 rows only
                    """,
                    {"sql_id": sql_id, "phv": int(plan_hash_value)},
                ),
                (
                    """
                    select other_xml
                    from dba_hist_sql_plan
                    where sql_id = :sql_id
                      and plan_hash_value = :phv
                      and dbid = (select dbid from v$database)
                      and other_xml is not null
                    order by id
                    fetch first 1 rows only
                    """,
                    {"sql_id": sql_id, "phv": int(plan_hash_value)},
                ),
                (
                    """
                    select other_xml
                    from gv$sql_plan
                    where plan_hash_value = :phv
                      and other_xml is not null
                    order by child_number, id
                    fetch first 1 rows only
                    """,
                    {"phv": int(plan_hash_value)},
                ),
                (
                    """
                    select other_xml
                    from dba_hist_sql_plan
                    where plan_hash_value = :phv
                      and dbid = (select dbid from v$database)
                      and other_xml is not null
                    order by id
                    fetch first 1 rows only
                    """,
                    {"phv": int(plan_hash_value)},
                ),
            ]
            for q, b in retrieval_paths:
                try:
                    xcols, xrows = _exec_query(cur, q, b)
                    if xrows:
                        ov = _rows_dict(xcols, xrows)[0].get("other_xml")
                        if ov:
                            other_xml = ov
                            break
                except Exception:
                    continue
            if not other_xml:
                return JSONFormatter.format_response(
                    {
                        "error": "Plan outline (other_xml) not found for provided SQL_ID/plan_hash_value.",
                        "sql_id": sql_id,
                        "plan_hash_value": int(plan_hash_value),
                        "available_plans": available_plans,
                    }
                )

            hints_sql = """
                select substr(extractvalue(value(d), '/hint'), 1, 4000) hint
                from table(xmlsequence(extract(xmltype(:other_xml), '/*/outline_data/hint'))) d
            """
            hcols, hrows = _exec_query(cur, hints_sql, {"other_xml": other_xml})
            hints = [str(r.get("hint")) for r in _rows_dict(hcols, hrows) if r.get("hint")]
            if not hints:
                return JSONFormatter.format_response(
                    {
                        "error": "No outline hints extracted from other_xml for selected plan.",
                        "sql_id": sql_id,
                        "plan_hash_value": int(plan_hash_value),
                    }
                )

            prof_name = profile_name or f"coe_{sql_id}_{int(plan_hash_value)}"
            sig_sql = """
                select
                    dbms_sqltune.sqltext_to_signature(:sql_txt) signature_exact,
                    dbms_sqltune.sqltext_to_signature(:sql_txt, 1) signature_force
                from dual
            """
            scols, srows = _exec_query(cur, sig_sql, {"sql_txt": sql_text})
            sig_info = _rows_dict(scols, srows)[0] if srows else {}

            script_lines: List[str] = [
                "set serveroutput on size unlimited",
                "whenever sqlerror exit sql.sqlcode",
                "declare",
                "  sql_txt clob;",
                "  h sys.sqlprof_attr;",
                "begin",
                "  dbms_lob.createtemporary(sql_txt, true);",
                "  dbms_lob.open(sql_txt, dbms_lob.lob_readwrite);",
            ]
            for chunk in _chunk_text(str(sql_text), 1000):
                qq = _q_quote(chunk)
                script_lines.append(f"  dbms_lob.writeappend(sql_txt, length({qq}), {qq});")
            script_lines.extend(
                [
                    "  dbms_lob.close(sql_txt);",
                    "  h := sys.sqlprof_attr(",
                    f"    {_q_quote('BEGIN_OUTLINE_DATA')},",
                ]
            )
            for hint in hints:
                for hint_chunk in _chunk_text(hint, 500):
                    script_lines.append(f"    {_q_quote(hint_chunk)},")
            script_lines.extend(
                [
                    f"    {_q_quote('END_OUTLINE_DATA')}",
                    "  );",
                    "  dbms_sqltune.import_sql_profile(",
                    "    sql_text    => sql_txt,",
                    "    profile     => h,",
                    f"    name        => {_q_quote(prof_name)},",
                    f"    description => {_q_quote(f'mcp {sql_id} {plan_hash_value} sig={sig_info.get('signature_exact')} sigf={sig_info.get('signature_force')}')},",
                    f"    category    => {_q_quote(category)},",
                    "    validate    => true,",
                    "    replace     => true,",
                    f"    force_match => {'true' if force_match else 'false'}",
                    "  );",
                    "  dbms_lob.freetemporary(sql_txt);",
                    "end;",
                    "/",
                ]
            )

            selected_plan = None
            for p in available_plans:
                if int(p.get("plan_hash_value") or -1) == int(plan_hash_value):
                    selected_plan = p
                    break

            return JSONFormatter.format_response(
                {
                    "sql_id": sql_id,
                    "plan_hash_value": int(plan_hash_value),
                    "profile_name": prof_name,
                    "force_match": bool(force_match),
                    "category": category,
                    "signatures": sig_info,
                    "selected_plan_metrics": selected_plan,
                    "available_plans": available_plans,
                    "outline_hints_count": len(hints),
                    "create_sql_profile_script": "\n".join(script_lines),
                    "notes": [
                        "This uses DBMS_SQLTUNE.IMPORT_SQL_PROFILE and requires Oracle Tuning Pack licensing.",
                        "SQL Profile influences optimizer costing; for hard pinning use SQL Plan Baseline (SPM fixed=YES).",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_analyze_awr_report(
    report_text: Optional[str] = None,
    report_path: Optional[str] = None,
    begin_snap_id: Optional[int] = None,
    end_snap_id: Optional[int] = None,
    window_minutes: Optional[int] = None,
    window_hours: Optional[int] = None,
    dbid: Optional[int] = None,
    instance_number: Optional[int] = None,
) -> str:
    """
    Analyze AWR in one call:
    - from provided report_text/report_path, or
    - generate from begin_snap_id/end_snap_id, or
    - auto-discover snapshots from a time window (window_minutes/window_hours).
    """
    awr_source: Dict[str, Any] = {"mode": "input"}

    # If no text/path is provided, generate report text from snapshots or time window.
    if report_text is None and report_path is None:
        resolved_begin = begin_snap_id
        resolved_end = end_snap_id

        if resolved_begin is None or resolved_end is None:
            mins = None
            if window_minutes is not None:
                mins = max(1, int(window_minutes))
            elif window_hours is not None:
                mins = max(1, int(window_hours)) * 60

            if mins is None:
                return JSONFormatter.format_response(
                    {
                        "error": (
                            "Provide either report_text/report_path, explicit begin_snap_id/end_snap_id, "
                            "or time window (window_minutes/window_hours)."
                        )
                    }
                )

            conn = _connect()
            try:
                with conn.cursor() as cur:
                    where = "where begin_interval_time >= systimestamp - numtodsinterval(:mins, 'MINUTE')"
                    binds: Dict[str, Any] = {"mins": mins}
                    if dbid is not None:
                        where += " and dbid = :dbid"
                        binds["dbid"] = int(dbid)
                    if instance_number is not None:
                        where += " and instance_number = :inst"
                        binds["inst"] = int(instance_number)

                    _execute_compat(
                        cur,
                        f"""
                        select snap_id
                        from dba_hist_snapshot
                        {where}
                        order by snap_id
                        """,
                        binds,
                    )
                    snap_ids = [int(r[0]) for r in cur.fetchall()]

                    if len(snap_ids) >= 2:
                        resolved_begin, resolved_end = snap_ids[0], snap_ids[-1]
                    else:
                        # Fallback to latest two snapshots in current context.
                        _execute_compat(
                            cur,
                            """
                            select snap_id
                            from (
                                select snap_id
                                from dba_hist_snapshot
                                order by snap_id desc
                            )
                            where rownum <= 2
                            """,
                        )
                        latest = [int(r[0]) for r in cur.fetchall()]
                        if len(latest) >= 2:
                            latest = sorted(latest)
                            resolved_begin, resolved_end = latest[0], latest[1]
                        else:
                            return JSONFormatter.format_response(
                                {
                                    "error": "Unable to find enough snapshots for requested time window.",
                                    "window_minutes": mins,
                                    "candidate_snapshots": snap_ids,
                                }
                            )
            finally:
                conn.close()

            awr_source = {
                "mode": "time_window",
                "window_minutes": mins,
                "resolved_begin_snap_id": resolved_begin,
                "resolved_end_snap_id": resolved_end,
            }
        else:
            awr_source = {
                "mode": "explicit_snapshots",
                "resolved_begin_snap_id": int(resolved_begin),
                "resolved_end_snap_id": int(resolved_end),
            }

        gen_json = await oracle_get_awr_report_text.fn(
            begin_snap_id=int(resolved_begin),
            end_snap_id=int(resolved_end),
            dbid=dbid,
            instance_number=instance_number,
        )
        try:
            gen_obj = json.loads(gen_json)
        except Exception as e:
            return JSONFormatter.format_response(
                {
                    "error": "Failed to parse generated AWR report payload.",
                    "details": str(e),
                }
            )
        if gen_obj.get("error"):
            return JSONFormatter.format_response(
                {
                    "error": "Failed to generate AWR report text.",
                    "details": gen_obj,
                    "awr_source": awr_source,
                }
            )
        report_text = gen_obj.get("report_text")
        if not report_text:
            return JSONFormatter.format_response(
                {
                    "error": "Generated AWR report text was empty.",
                    "awr_source": awr_source,
                }
            )
        awr_source["generation"] = {
            "dbid": gen_obj.get("dbid"),
            "instance_number": gen_obj.get("instance_number"),
            "begin_snap_id": gen_obj.get("begin_snap_id"),
            "end_snap_id": gen_obj.get("end_snap_id"),
        }

    content = _read_report_input(report_text=report_text, report_path=report_path)
    metrics = _parse_awr_metrics(content)

    findings: List[str] = []
    if metrics.get("db_time_s") and metrics.get("db_cpu_s"):
        if metrics["db_cpu_s"] > 0 and metrics["db_time_s"] / metrics["db_cpu_s"] > 2.0:
            findings.append("DB Time is much higher than DB CPU; wait events likely dominate response time.")

    if metrics.get("top_wait_events"):
        top_event = metrics["top_wait_events"][0]["event"]
        findings.append(f"Top observed wait event: {top_event}")

    if not findings:
        findings.append("Limited structured metrics found; provide fuller AWR text export for deeper analysis.")

    return JSONFormatter.format_response(
        {
            "awr_source": awr_source,
            "summary_metrics": metrics,
            "findings": findings,
            "recommended_actions": [
                "Inspect top SQL by DB Time and parse plan changes between snapshots.",
                "Correlate top waits with storage, I/O, and application concurrency behavior.",
                "Validate stats freshness and segment/index growth during the interval.",
            ],
        },
        optimize=True,
    )


@mcp.tool()
@trace_tool
async def oracle_compare_awr_reports(
    baseline_report_text: Optional[str] = None,
    baseline_report_path: Optional[str] = None,
    target_report_text: Optional[str] = None,
    target_report_path: Optional[str] = None,
    begin_snap_id_1: Optional[int] = None,
    end_snap_id_1: Optional[int] = None,
    begin_snap_id_2: Optional[int] = None,
    end_snap_id_2: Optional[int] = None,
    begin_snap_id_baseline: Optional[int] = None,
    end_snap_id_baseline: Optional[int] = None,
    begin_snap_id_target: Optional[int] = None,
    end_snap_id_target: Optional[int] = None,
    dbid: Optional[int] = None,
    instance_number: Optional[int] = None,
) -> str:
    """
    Compare two AWR reports and highlight metric deltas and potential regressions.
    """
    use_snapshot_windows = any(
        x is not None
        for x in (
            begin_snap_id_1,
            end_snap_id_1,
            begin_snap_id_2,
            end_snap_id_2,
            begin_snap_id_baseline,
            end_snap_id_baseline,
            begin_snap_id_target,
            end_snap_id_target,
        )
    )

    if use_snapshot_windows:
        b_begin = begin_snap_id_1 if begin_snap_id_1 is not None else begin_snap_id_baseline
        b_end = end_snap_id_1 if end_snap_id_1 is not None else end_snap_id_baseline
        t_begin = begin_snap_id_2 if begin_snap_id_2 is not None else begin_snap_id_target
        t_end = end_snap_id_2 if end_snap_id_2 is not None else end_snap_id_target

        if None in (b_begin, b_end, t_begin, t_end):
            return JSONFormatter.format_response(
                {
                    "error": (
                        "When using snapshot comparison, provide all baseline and target snap IDs. "
                        "Supported args: begin_snap_id_1/end_snap_id_1/begin_snap_id_2/end_snap_id_2 "
                        "or begin_snap_id_baseline/end_snap_id_baseline/begin_snap_id_target/end_snap_id_target."
                    )
                }
            )

        baseline_json = await oracle_get_awr_report_text(
            begin_snap_id=int(b_begin),
            end_snap_id=int(b_end),
            dbid=dbid,
            instance_number=instance_number,
        )
        target_json = await oracle_get_awr_report_text(
            begin_snap_id=int(t_begin),
            end_snap_id=int(t_end),
            dbid=dbid,
            instance_number=instance_number,
        )

        try:
            baseline_obj = json.loads(baseline_json)
            target_obj = json.loads(target_json)
        except Exception as parse_err:
            return JSONFormatter.format_response(
                {
                    "error": "Unable to parse generated AWR report payloads from snapshot windows.",
                    "details": str(parse_err),
                }
            )

        if baseline_obj.get("error"):
            return JSONFormatter.format_response(
                {"error": "Baseline AWR snapshot extraction failed.", "details": baseline_obj}
            )
        if target_obj.get("error"):
            return JSONFormatter.format_response(
                {"error": "Target AWR snapshot extraction failed.", "details": target_obj}
            )

        baseline_report_text = baseline_obj.get("report_text")
        target_report_text = target_obj.get("report_text")

        if not baseline_report_text or not target_report_text:
            return JSONFormatter.format_response(
                {
                    "error": "Generated AWR report text is empty for one or both windows.",
                    "baseline_window": {"begin_snap_id": b_begin, "end_snap_id": b_end},
                    "target_window": {"begin_snap_id": t_begin, "end_snap_id": t_end},
                }
            )

    baseline_content = _read_report_input(
        report_text=baseline_report_text,
        report_path=baseline_report_path,
    )
    target_content = _read_report_input(
        report_text=target_report_text,
        report_path=target_report_path,
    )

    base = _parse_awr_metrics(baseline_content)
    target = _parse_awr_metrics(target_content)

    comparisons = {
        "db_time_s": _compare_metric(base.get("db_time_s"), target.get("db_time_s")),
        "db_cpu_s": _compare_metric(base.get("db_cpu_s"), target.get("db_cpu_s")),
        "aas": _compare_metric(base.get("aas"), target.get("aas")),
        "elapsed_min": _compare_metric(base.get("elapsed_min"), target.get("elapsed_min")),
    }

    notes: List[str] = []
    db_time_pct = comparisons["db_time_s"].get("pct_change")
    if db_time_pct is not None:
        if db_time_pct > 20:
            notes.append("Potential regression: DB Time increased materially versus baseline.")
        elif db_time_pct < -20:
            notes.append("Performance improvement: DB Time reduced materially versus baseline.")

    if base.get("top_wait_events") and target.get("top_wait_events"):
        base_top = base["top_wait_events"][0]["event"]
        target_top = target["top_wait_events"][0]["event"]
        if base_top != target_top:
            notes.append(f"Top wait event changed from '{base_top}' to '{target_top}'.")

    if not notes:
        notes.append("No strong regression signal detected from parsed summary metrics.")

    return JSONFormatter.format_response(
        {
            "baseline": base,
            "target": target,
            "comparisons": comparisons,
            "assessment": notes,
            "next_steps": [
                "Compare top SQL_ID overlap and plan hash changes.",
                "Check whether workload mix or concurrency changed between windows.",
                "Validate system-level bottlenecks (CPU saturation, storage latency, network waits).",
            ],
        },
        optimize=True,
    )


@mcp.tool()
@trace_tool
async def oracle_get_awr_report_text(
    begin_snap_id: int,
    end_snap_id: int,
    dbid: Optional[int] = None,
    instance_number: Optional[int] = None,
) -> str:
    """
    Generate AWR report text directly from DBMS_WORKLOAD_REPOSITORY.AWR_REPORT_TEXT.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if dbid is None:
                _execute_compat(cur, "select dbid from v$database")
                dbid = int(cur.fetchone()[0])

            if instance_number is None:
                _execute_compat(cur, "select instance_number from v$instance")
                instance_number = int(cur.fetchone()[0])

            params = {
                "dbid": dbid,
                "instance_number": instance_number,
                "begin_snap_id": begin_snap_id,
                "end_snap_id": end_snap_id,
            }

            report_sql = """
                select output
                from table(
                    dbms_workload_repository.awr_report_text(
                        :dbid,
                        :instance_number,
                        :begin_snap_id,
                        :end_snap_id
                    )
                )
            """

            try:
                _execute_compat(cur, report_sql, params)
            except Exception as e:
                if "ORA-20019" not in str(e):
                    raise
                # In XE/CDB setups snapshots may exist only in root container context.
                cfg = _oracle_config()
                root_dsn = _root_service_dsn(cfg.dsn)
                if root_dsn != cfg.dsn:
                    try:
                        with oracledb.connect(
                            user=cfg.user,
                            password=cfg.password,
                            dsn=root_dsn,
                            tcp_connect_timeout=5,
                        ) as root_conn:
                            with root_conn.cursor() as rcur:
                                _execute_compat(rcur, "select dbid from v$database")
                                root_dbid = int(rcur.fetchone()[0])
                                _execute_compat(rcur, "select instance_number from v$instance")
                                root_inst = int(rcur.fetchone()[0])
                                _execute_compat(rcur, 
                                    """
                                    select snap_id
                                    from (
                                        select snap_id
                                        from dba_hist_snapshot
                                        where dbid = :dbid
                                          and instance_number = :instance_number
                                        order by snap_id desc
                                    )
                                    fetch first 2 rows only
                                    """,
                                    {"dbid": root_dbid, "instance_number": root_inst},
                                )
                                root_snaps = [int(r[0]) for r in rcur.fetchall()]
                                if len(root_snaps) >= 2:
                                    root_snaps = sorted(root_snaps)
                                    _execute_compat(rcur, 
                                        report_sql,
                                        {
                                            "dbid": root_dbid,
                                            "instance_number": root_inst,
                                            "begin_snap_id": root_snaps[0],
                                            "end_snap_id": root_snaps[1],
                                        },
                                    )
                                    lines = [str(r[0]) for r in rcur.fetchall()]
                                    return JSONFormatter.format_response(
                                        {
                                            "dbid": root_dbid,
                                            "instance_number": root_inst,
                                            "begin_snap_id": root_snaps[0],
                                            "end_snap_id": root_snaps[1],
                                            "report_text": "".join(lines),
                                            "note": f"report generated from root service dsn '{root_dsn}'",
                                        },
                                        optimize=True,
                                    )
                    except Exception:
                        pass

                # Last fallback: retry with latest two snapshots in current session.
                _execute_compat(cur, 
                    """
                    select snap_id
                    from (
                        select snap_id
                        from dba_hist_snapshot
                        where dbid = :dbid
                          and instance_number = :instance_number
                        order by snap_id desc
                    )
                    fetch first 2 rows only
                    """,
                    {"dbid": dbid, "instance_number": instance_number},
                )
                snap_rows = [int(r[0]) for r in cur.fetchall()]
                if len(snap_rows) < 2:
                    return JSONFormatter.format_response(
                        {
                            "error": "Unable to generate AWR report: not enough valid snapshots in current context.",
                            "dbid": dbid,
                            "instance_number": instance_number,
                            "candidate_snap_ids": snap_rows,
                            "note": "Try calling with a wider snapshot range or use root service (XE).",
                        }
                    )
                snap_rows = sorted(snap_rows)
                params["begin_snap_id"] = snap_rows[0]
                params["end_snap_id"] = snap_rows[1]
                try:
                    _execute_compat(cur, report_sql, params)
                except Exception as last_err:
                    return JSONFormatter.format_response(
                        {
                            "error": "Unable to generate AWR report in current context.",
                            "details": str(last_err),
                            "dbid": dbid,
                            "instance_number": instance_number,
                            "begin_snap_id": params["begin_snap_id"],
                            "end_snap_id": params["end_snap_id"],
                            "note": "Snapshots may belong to a different container/context in XE.",
                        }
                    )

            lines = [str(r[0]) for r in cur.fetchall()]
            return JSONFormatter.format_response(
                {
                    "dbid": dbid,
                    "instance_number": instance_number,
                    "begin_snap_id": params["begin_snap_id"],
                    "end_snap_id": params["end_snap_id"],
                    "report_text": "".join(lines),
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_waits_hotspots(hours: int = 1, top_n: int = 15) -> str:
    """
    Analyze wait hotspots from ASH and return top wait classes/events, SQL IDs, and modules.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            binds = {"hours": max(1, hours), "top_n": max(1, top_n)}

            wait_sql = """
                select wait_class, event, count(*) samples
                from v$active_session_history
                where sample_time >= systimestamp - numtodsinterval(:hours, 'HOUR')
                group by wait_class, event
                order by samples desc
                fetch first :top_n rows only
            """
            sqlid_sql = """
                select sql_id, count(*) samples
                from v$active_session_history
                where sample_time >= systimestamp - numtodsinterval(:hours, 'HOUR')
                  and sql_id is not null
                group by sql_id
                order by samples desc
                fetch first :top_n rows only
            """
            module_sql = """
                select nvl(module, 'UNKNOWN') module, nvl(machine, 'UNKNOWN') machine, count(*) samples
                from v$active_session_history
                where sample_time >= systimestamp - numtodsinterval(:hours, 'HOUR')
                group by module, machine
                order by samples desc
                fetch first :top_n rows only
            """

            waits_cols, waits_rows = _exec_query(cur, wait_sql, binds)
            sql_cols, sql_rows = _exec_query(cur, sqlid_sql, binds)
            module_cols, module_rows = _exec_query(cur, module_sql, binds)

            return JSONFormatter.format_response(
                {
                    "window_hours": hours,
                    "top_wait_events": _rows_dict(waits_cols, waits_rows),
                    "top_sql_ids": _rows_dict(sql_cols, sql_rows),
                    "top_modules": _rows_dict(module_cols, module_rows),
                    "recommendations": [
                        "Correlate top wait events with top SQL_IDs before tuning.",
                        "Check module/machine concentration for app-tier hotspots.",
                        "Validate if waits are stable or spike-only using shorter windows.",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_blocking_sessions_analyzer(top_n: int = 20) -> str:
    """
    Identify blockers/waiters and suggest safe remediation order.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            block_sql = """
                select
                    s.inst_id,
                    s.sid,
                    s.serial#,
                    s.username,
                    s.status,
                    s.program,
                    s.module,
                    s.machine,
                    s.seconds_in_wait,
                    s.event,
                    s.blocking_instance,
                    s.blocking_session
                from gv$session s
                where s.type = 'USER'
                  and s.blocking_session is not null
                order by s.seconds_in_wait desc
                fetch first :top_n rows only
            """
            blocker_sql = """
                select
                    b.inst_id,
                    b.sid,
                    b.serial#,
                    b.username,
                    b.status,
                    b.program,
                    b.module,
                    b.machine,
                    b.sql_id
                from gv$session b
                where (b.inst_id, b.sid) in (
                    select distinct blocking_instance, blocking_session
                    from gv$session
                    where blocking_session is not null
                )
                fetch first :top_n rows only
            """

            wait_cols, wait_rows = _exec_query(cur, block_sql, {"top_n": max(1, top_n)})
            blocker_cols, blocker_rows = _exec_query(cur, blocker_sql, {"top_n": max(1, top_n)})

            kill_candidates = []
            for row in _rows_dict(blocker_cols, blocker_rows):
                if (row.get("username") or "").upper() in {"SYS", "SYSTEM"}:
                    continue
                kill_candidates.append(
                    {
                        "inst_id": row.get("inst_id"),
                        "sid": row.get("sid"),
                        "serial#": row.get("serial#"),
                        "username": row.get("username"),
                        "sql_id": row.get("sql_id"),
                        "kill_syntax": f"alter system kill session '{row.get('sid')},{row.get('serial#')},@{row.get('inst_id')}' immediate",
                    }
                )

            return JSONFormatter.format_response(
                {
                    "blocked_sessions": _rows_dict(wait_cols, wait_rows),
                    "blocking_sessions": _rows_dict(blocker_cols, blocker_rows),
                    "potential_kill_candidates": kill_candidates[:top_n],
                    "caution": "Validate business impact before killing sessions. Avoid killing SYS/SYSTEM/background sessions.",
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_role_privilege_audit(
    username: Optional[str] = None,
    include_object_privileges: bool = False,
    top_n: int = 200,
) -> str:
    """
    Audit user roles and privileges, highlighting risky grants.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            where = ""
            binds: Dict[str, Any] = {"top_n": max(1, top_n)}
            if username:
                where = "where grantee = upper(:username)"
                binds["username"] = username

            role_sql = f"""
                select grantee, granted_role, admin_option, default_role
                from dba_role_privs
                {where}
                fetch first :top_n rows only
            """
            sys_sql = f"""
                select grantee, privilege, admin_option
                from dba_sys_privs
                {where}
                fetch first :top_n rows only
            """

            role_cols, role_rows = _exec_query(cur, role_sql, binds)
            sys_cols, sys_rows = _exec_query(cur, sys_sql, binds)

            obj_cols: List[str] = []
            obj_rows: List[Tuple[Any, ...]] = []
            if include_object_privileges:
                obj_sql = f"""
                    select grantee, owner, table_name, privilege, grantable
                    from dba_tab_privs
                    {where}
                    fetch first :top_n rows only
                """
                obj_cols, obj_rows = _exec_query(cur, obj_sql, binds)

            risky = []
            for row in _rows_dict(sys_cols, sys_rows):
                p = (row.get("privilege") or "").upper()
                if p in {"DBA", "ALTER SYSTEM", "CREATE ANY TABLE", "DROP ANY TABLE", "SELECT ANY TABLE"} or "ANY" in p:
                    risky.append(row)
            for row in _rows_dict(role_cols, role_rows):
                if (row.get("granted_role") or "").upper() in {"DBA", "DATAPUMP_EXP_FULL_DATABASE", "DATAPUMP_IMP_FULL_DATABASE"}:
                    risky.append(row)

            payload = {
                "roles": _rows_dict(role_cols, role_rows),
                "system_privileges": _rows_dict(sys_cols, sys_rows),
                "risky_grants": risky,
                "recommendations": [
                    "Enforce least privilege and remove ANY privileges where possible.",
                    "Review ADMIN OPTION grants and PUBLIC grants as a separate pass.",
                ],
            }
            if include_object_privileges:
                payload["object_privileges"] = _rows_dict(obj_cols, obj_rows)
            return JSONFormatter.format_response(payload, optimize=True)
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_schema_drift_checker(schema_a: str, schema_b: str) -> str:
    """
    Compare object inventory and invalid objects between two schemas in the same database.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            obj_sql = """
                select owner, object_type, count(*) object_count
                from dba_objects
                where owner in (upper(:schema_a), upper(:schema_b))
                group by owner, object_type
                order by owner, object_type
            """
            inv_sql = """
                select owner, object_type, object_name, status
                from dba_objects
                where owner in (upper(:schema_a), upper(:schema_b))
                  and status <> 'VALID'
                order by owner, object_type, object_name
                fetch first 500 rows only
            """
            cols, rows = _exec_query(cur, obj_sql, {"schema_a": schema_a, "schema_b": schema_b})
            inv_cols, inv_rows = _exec_query(cur, inv_sql, {"schema_a": schema_a, "schema_b": schema_b})

            summary: Dict[str, Dict[str, int]] = {schema_a.upper(): {}, schema_b.upper(): {}}
            for r in _rows_dict(cols, rows):
                owner = str(r["owner"]).upper()
                summary.setdefault(owner, {})
                summary[owner][r["object_type"]] = int(r["object_count"])

            deltas = []
            a_owner = schema_a.upper()
            b_owner = schema_b.upper()
            all_types = sorted(set(summary.get(a_owner, {}).keys()) | set(summary.get(b_owner, {}).keys()))
            for t in all_types:
                a_cnt = summary.get(a_owner, {}).get(t, 0)
                b_cnt = summary.get(b_owner, {}).get(t, 0)
                if a_cnt != b_cnt:
                    deltas.append({"object_type": t, "schema_a_count": a_cnt, "schema_b_count": b_cnt, "delta": b_cnt - a_cnt})

            return JSONFormatter.format_response(
                {
                    "schema_a": a_owner,
                    "schema_b": b_owner,
                    "object_count_summary": summary,
                    "count_deltas": deltas,
                    "invalid_objects": _rows_dict(inv_cols, inv_rows),
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_sql_plan_regression_detector(
    days: int = 7,
    top_n: int = 20,
    window_minutes: Optional[int] = None,
) -> str:
    """
    Detect potential SQL plan regressions from AWR SQL stats by comparing best vs worst plans.
    Optional window_minutes allows short windows (for example 30 minutes).
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if window_minutes is not None and int(window_minutes) <= 0:
                raise ValueError("window_minutes must be > 0 when provided.")

            window_clause = (
                "sn.begin_interval_time >= systimestamp - numtodsinterval(:window_minutes, 'MINUTE')"
                if window_minutes is not None
                else "sn.begin_interval_time >= systimestamp - numtodsinterval(:days, 'DAY')"
            )
            sql = """
                with s as (
                    select
                        h.sql_id,
                        h.plan_hash_value,
                        sum(h.executions_delta) execs,
                        sum(h.elapsed_time_delta) elapsed_us,
                        sum(h.cpu_time_delta) cpu_us,
                        sum(h.buffer_gets_delta) buffer_gets,
                        sum(h.disk_reads_delta) disk_reads,
                        sum(h.rows_processed_delta) rows_processed
                    from dba_hist_sqlstat h
                    join dba_hist_snapshot sn
                      on sn.snap_id = h.snap_id
                     and sn.dbid = h.dbid
                     and sn.instance_number = h.instance_number
                    where __WINDOW_CLAUSE__
                    group by h.sql_id, h.plan_hash_value
                ),
                p as (
                    select
                        sql_id,
                        plan_hash_value,
                        execs,
                        elapsed_us,
                        cpu_us,
                        buffer_gets,
                        disk_reads,
                        rows_processed,
                        case when execs > 0 then elapsed_us / execs else null end elapsed_us_per_exec,
                        case when execs > 0 then cpu_us / execs else null end cpu_us_per_exec,
                        case when execs > 0 then buffer_gets / execs else null end lio_per_exec,
                        case when execs > 0 then disk_reads / execs else null end pio_per_exec,
                        case when execs > 0 then rows_processed / execs else null end rows_per_exec
                    from s
                ),
                a as (
                    select
                        sql_id,
                        count(*) plan_count,
                        min(elapsed_us_per_exec) best_elapsed_us_per_exec,
                        max(elapsed_us_per_exec) worst_elapsed_us_per_exec
                    from p
                    where elapsed_us_per_exec is not null
                    group by sql_id
                    having count(*) > 1
                ),
                ranked as (
                    select
                        p.sql_id,
                        p.plan_hash_value,
                        p.execs,
                        p.elapsed_us_per_exec,
                        row_number() over (
                            partition by p.sql_id
                            order by p.elapsed_us_per_exec asc nulls last, p.execs desc
                        ) as rn_best,
                        row_number() over (
                            partition by p.sql_id
                            order by p.elapsed_us_per_exec desc nulls last, p.execs desc
                        ) as rn_worst
                    from p
                ),
                best_plan as (
                    select sql_id, plan_hash_value best_plan_hash_value
                    from ranked
                    where rn_best = 1
                ),
                worst_plan as (
                    select sql_id, plan_hash_value worst_plan_hash_value
                    from ranked
                    where rn_worst = 1
                )
                select
                    a.sql_id,
                    a.plan_count,
                    bp.best_plan_hash_value,
                    wp.worst_plan_hash_value,
                    round(a.best_elapsed_us_per_exec, 2) best_elapsed_us_per_exec,
                    round(a.worst_elapsed_us_per_exec, 2) worst_elapsed_us_per_exec,
                    round(case when a.best_elapsed_us_per_exec > 0 then a.worst_elapsed_us_per_exec / a.best_elapsed_us_per_exec else null end, 2) ratio
                from a
                join best_plan bp on bp.sql_id = a.sql_id
                join worst_plan wp on wp.sql_id = a.sql_id
                order by ratio desc nulls last
                fetch first :top_n rows only
            """
            sql = sql.replace("__WINDOW_CLAUSE__", window_clause)
            binds = {"top_n": max(1, top_n)}
            if window_minutes is not None:
                binds["window_minutes"] = int(window_minutes)
            else:
                binds["days"] = max(1, days)
            cols, rows = _exec_query(cur, sql, binds)
            regressions = _rows_dict(cols, rows)

            for item in regressions:
                detail_sql = """
                    with p as (
                        select
                            h.plan_hash_value,
                            sum(h.executions_delta) execs,
                            sum(h.elapsed_time_delta) elapsed_us,
                            sum(h.cpu_time_delta) cpu_us,
                            sum(h.buffer_gets_delta) buffer_gets,
                            sum(h.disk_reads_delta) disk_reads,
                            sum(h.rows_processed_delta) rows_processed
                        from dba_hist_sqlstat h
                        join dba_hist_snapshot sn
                          on sn.snap_id = h.snap_id
                         and sn.dbid = h.dbid
                         and sn.instance_number = h.instance_number
                        where h.sql_id = :sql_id
                          and __WINDOW_CLAUSE__
                        group by h.plan_hash_value
                    )
                    select
                        plan_hash_value,
                        execs,
                        round(case when execs > 0 then (elapsed_us / execs) / 1000 end, 3) elapsed_ms_per_exec,
                        round(case when execs > 0 then (cpu_us / execs) / 1000 end, 3) cpu_ms_per_exec,
                        round(case when execs > 0 then buffer_gets / execs end, 2) lio_per_exec,
                        round(case when execs > 0 then disk_reads / execs end, 2) pio_per_exec,
                        round(case when execs > 0 then rows_processed / execs end, 2) rows_per_exec
                    from p
                    where execs > 0
                    order by elapsed_ms_per_exec nulls last, execs desc
                """
                detail_sql = detail_sql.replace("__WINDOW_CLAUSE__", window_clause)
                detail_binds = {"sql_id": item["sql_id"]}
                if window_minutes is not None:
                    detail_binds["window_minutes"] = int(window_minutes)
                else:
                    detail_binds["days"] = max(1, days)
                dcols, drows = _exec_query(cur, detail_sql, detail_binds)
                plans = _rows_dict(dcols, drows)

                if plans:
                    min_elapsed = min(float(p["elapsed_ms_per_exec"]) for p in plans if p.get("elapsed_ms_per_exec") is not None) or 1.0
                    min_cpu = min(float(p["cpu_ms_per_exec"]) for p in plans if p.get("cpu_ms_per_exec") is not None) or 1.0
                    min_lio = min(float(p["lio_per_exec"]) for p in plans if p.get("lio_per_exec") is not None) or 1.0
                    min_pio = min(float(p["pio_per_exec"]) for p in plans if p.get("pio_per_exec") is not None) or 1.0
                    for plan in plans:
                        elapsed = float(plan.get("elapsed_ms_per_exec") or min_elapsed)
                        cpu = float(plan.get("cpu_ms_per_exec") or min_cpu)
                        lio = float(plan.get("lio_per_exec") or min_lio)
                        pio = float(plan.get("pio_per_exec") or min_pio)
                        # Lower score is better; elapsed/cpu weighted highest.
                        plan["plan_score"] = round(
                            (elapsed / min_elapsed) * 0.45
                            + (cpu / min_cpu) * 0.30
                            + (lio / min_lio) * 0.15
                            + (pio / min_pio) * 0.10,
                            3,
                        )
                    plans = sorted(
                        plans,
                        key=lambda x: (float(x.get("plan_score") or 999999), -(x.get("execs") or 0)),
                    )
                    best = plans[0]
                    item["plans_by_hash"] = plans
                    item["recommended_plan_hash_value"] = best.get("plan_hash_value")
                    item["recommended_plan_reason"] = (
                        "Lowest composite score from elapsed/cpu/lio/pio per exec; "
                        f"elapsed_ms_per_exec={best.get('elapsed_ms_per_exec')}, "
                        f"cpu_ms_per_exec={best.get('cpu_ms_per_exec')}"
                    )

            return JSONFormatter.format_response(
                {
                    "window": (
                        {"minutes": int(window_minutes)}
                        if window_minutes is not None
                        else {"days": int(max(1, days))}
                    ),
                    "potential_regressions": regressions,
                    "recommendations": [
                        "For high-ratio SQL_IDs, compare plans with DBMS_XPLAN from AWR and cursor cache.",
                        "Prefer SPM baselines to pin a proven plan; SQL Profiles influence costing but do not strictly pin one plan hash.",
                        "Before pinning, validate stats freshness and object/index changes in the same interval.",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_sql_plan_rescue_playbook(
    sql_id: str,
    lookback_days: int = 14,
    preferred_plan_hash_value: Optional[int] = None,
    top_plans: int = 10,
) -> str:
    """
    Build a recovery playbook for SQL plan regressions: find best historical plan,
    generate SPM pinning SQL, and generate targeted shared-pool purge commands.
    """
    sql_id = _validate_sql_id(sql_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            current_sql = """
                select
                    inst_id,
                    child_number,
                    plan_hash_value,
                    executions,
                    round(elapsed_time / nullif(executions, 0) / 1000000, 6) sec_per_exec,
                    parsing_schema_name,
                    module,
                    to_char(last_active_time, 'YYYY-MM-DD HH24:MI:SS') last_active_time
                from gv$sql
                where sql_id = :sql_id
                order by last_active_time desc
                fetch first :top_n rows only
            """
            cur_cols, cur_rows = _exec_query(
                cur,
                current_sql,
                {"sql_id": sql_id, "top_n": max(1, top_plans)},
            )

            hist_sql = """
                with p as (
                    select
                        h.plan_hash_value,
                        sum(h.executions_delta) execs,
                        sum(h.elapsed_time_delta) elapsed_us,
                        sum(h.buffer_gets_delta) buffer_gets,
                        min(sn.begin_interval_time) first_seen,
                        max(sn.end_interval_time) last_seen
                    from dba_hist_sqlstat h
                    join dba_hist_snapshot sn
                      on sn.snap_id = h.snap_id
                     and sn.dbid = h.dbid
                     and sn.instance_number = h.instance_number
                    where h.sql_id = :sql_id
                      and sn.begin_interval_time >= systimestamp - numtodsinterval(:days, 'DAY')
                    group by h.plan_hash_value
                )
                select
                    plan_hash_value,
                    execs,
                    round(elapsed_us / 1000000, 3) total_elapsed_s,
                    round(case when execs > 0 then (elapsed_us / execs) / 1000000 end, 6) sec_per_exec,
                    round(case when execs > 0 then buffer_gets / execs end, 2) lio_per_exec,
                    to_char(first_seen, 'YYYY-MM-DD HH24:MI:SS') first_seen,
                    to_char(last_seen, 'YYYY-MM-DD HH24:MI:SS') last_seen
                from p
                order by sec_per_exec nulls last, execs desc
                fetch first :top_n rows only
            """
            hist_cols, hist_rows = _exec_query(
                cur,
                hist_sql,
                {"sql_id": sql_id, "days": max(1, lookback_days), "top_n": max(1, top_plans)},
            )
            hist_data = _rows_dict(hist_cols, hist_rows)
            plan_stats_source = "awr"
            if not hist_data:
                cache_sql = """
                    select
                        plan_hash_value,
                        sum(executions) execs,
                        round(sum(elapsed_time) / 1000000, 3) total_elapsed_s,
                        round(case when sum(executions) > 0 then (sum(elapsed_time) / sum(executions)) / 1000000 end, 6) sec_per_exec,
                        round(case when sum(executions) > 0 then (sum(buffer_gets) / sum(executions)) end, 2) lio_per_exec,
                        null first_seen,
                        null last_seen
                    from gv$sql
                    where sql_id = :sql_id
                    group by plan_hash_value
                    order by sec_per_exec nulls last, execs desc
                    fetch first :top_n rows only
                """
                c_cols, c_rows = _exec_query(
                    cur,
                    cache_sql,
                    {"sql_id": sql_id, "top_n": max(1, top_plans)},
                )
                hist_data = _rows_dict(c_cols, c_rows)
                plan_stats_source = "cursor_cache"
            if not hist_data:
                return JSONFormatter.format_response(
                    {
                        "sql_id": sql_id,
                        "error": "No plan data found in AWR or cursor cache for this SQL_ID.",
                        "lookback_days": lookback_days,
                    }
                )

            candidate = None
            if preferred_plan_hash_value is not None:
                for r in hist_data:
                    if int(r.get("plan_hash_value") or -1) == int(preferred_plan_hash_value):
                        candidate = r
                        break
                if candidate is None:
                    return JSONFormatter.format_response(
                        {
                            "sql_id": sql_id,
                            "error": "preferred_plan_hash_value not found in historical plan set.",
                            "preferred_plan_hash_value": preferred_plan_hash_value,
                            "historical_plans": hist_data,
                        }
                    )
            else:
                ranked = [r for r in hist_data if r.get("sec_per_exec") is not None]
                candidate = ranked[0] if ranked else hist_data[0]

            chosen_phv = int(candidate.get("plan_hash_value"))

            worst = None
            ranked_desc = [r for r in hist_data if r.get("sec_per_exec") is not None]
            ranked_desc = sorted(ranked_desc, key=lambda x: float(x["sec_per_exec"]), reverse=True)
            if ranked_desc:
                worst = ranked_desc[0]

            ratio = None
            if worst and candidate.get("sec_per_exec") and float(candidate["sec_per_exec"]) > 0:
                ratio = round(float(worst["sec_per_exec"]) / float(candidate["sec_per_exec"]), 2)

            snaps_sql = """
                select min(h.snap_id) begin_snap_id, max(h.snap_id) end_snap_id
                from dba_hist_sqlstat h
                join dba_hist_snapshot sn
                  on sn.snap_id = h.snap_id
                 and sn.dbid = h.dbid
                 and sn.instance_number = h.instance_number
                where h.sql_id = :sql_id
                  and h.plan_hash_value = :phv
                  and sn.begin_interval_time >= systimestamp - numtodsinterval(:days, 'DAY')
            """
            snap_cols, snap_rows = _exec_query(
                cur,
                snaps_sql,
                {"sql_id": sql_id, "phv": chosen_phv, "days": max(1, lookback_days)},
            )
            snap_data = _rows_dict(snap_cols, snap_rows)[0] if snap_rows else {}
            begin_snap_id = snap_data.get("begin_snap_id")
            end_snap_id = snap_data.get("end_snap_id")

            text_sql = """
                select sql_fulltext
                from v$sqlarea
                where sql_id = :sql_id
                  and rownum = 1
            """
            sql_text = None
            try:
                t_cols, t_rows = _exec_query(cur, text_sql, {"sql_id": sql_id})
                if t_rows:
                    row = _rows_dict(t_cols, t_rows)[0]
                    sql_text = row.get("sql_fulltext")
            except Exception:
                sql_text = None

            if not sql_text:
                try:
                    htext_sql = """
                        select sql_text
                        from dba_hist_sqltext
                        where sql_id = :sql_id
                          and rownum = 1
                    """
                    ht_cols, ht_rows = _exec_query(cur, htext_sql, {"sql_id": sql_id})
                    if ht_rows:
                        sql_text = _rows_dict(ht_cols, ht_rows)[0].get("sql_text")
                except Exception:
                    sql_text = None

            basic_filter = f"sql_id = '{sql_id}' and plan_hash_value = {chosen_phv}"

            spm_block = [
                "declare",
                "  l_loaded number := 0;",
                "begin",
                f"  l_loaded := dbms_spm.load_plans_from_cursor_cache(sql_id => '{sql_id}', plan_hash_value => {chosen_phv}, fixed => 'YES', enabled => 'YES');",
                "  if l_loaded = 0 then",
            ]
            if begin_snap_id is not None and end_snap_id is not None:
                spm_block.append(
                    "    l_loaded := dbms_spm.load_plans_from_awr("
                    f"begin_snap => {int(begin_snap_id)}, end_snap => {int(end_snap_id)}, "
                    f"basic_filter => q'[{basic_filter}]', fixed => 'YES', enabled => 'YES');"
                )
            else:
                spm_block.append("    -- Could not derive AWR snapshot window automatically for this plan.")
                spm_block.append("    -- If needed, run load_plans_from_awr manually with begin_snap/end_snap.")
            spm_block.extend(
                [
                    "  end if;",
                    "  dbms_output.put_line('Plans loaded: ' || l_loaded);",
                    "end;",
                    "/",
                ]
            )

            verify_sql = [
                (
                    "select b.sql_handle, b.plan_name, b.enabled, b.accepted, b.fixed "
                    "from dba_sql_plan_baselines b "
                    "join v$sqlarea v on v.exact_matching_signature = b.signature "
                    f"where v.sql_id = '{sql_id}'"
                ),
                f"select inst_id, child_number, plan_hash_value, executions, round(elapsed_time/nullif(executions,0)/1000000,6) sec_per_exec from gv$sql where sql_id = '{sql_id}' order by last_active_time desc;",
            ]

            purge_sql = [
                f"select inst_id, address, hash_value from gv$sqlarea where sql_id = '{sql_id}';",
                "-- Run per instance where the cursor exists:",
                "exec dbms_shared_pool.purge('<ADDRESS>,<HASH_VALUE>', 'C');",
            ]

            notes = [
                "For pinning a specific execution plan hash, SQL Plan Baseline (SPM fixed=YES) is the reliable mechanism.",
                "SQL Profiles influence optimizer estimates but do not strictly guarantee a specific plan hash value.",
                "Avoid full shared pool flush; use targeted DBMS_SHARED_POOL.PURGE for the SQL cursor.",
            ]

            if ratio and ratio >= 2:
                notes.append(f"Observed worst/best sec_per_exec ratio is ~{ratio}x in the lookback window.")

            return JSONFormatter.format_response(
                {
                    "sql_id": sql_id,
                    "lookback_days": lookback_days,
                    "plan_stats_source": plan_stats_source,
                    "current_cursor_plans": _rows_dict(cur_cols, cur_rows),
                    "historical_plan_stats": hist_data,
                    "recommended_old_plan_hash_value": chosen_phv,
                    "worst_to_best_sec_per_exec_ratio": ratio,
                    "recommended_awr_window_for_plan": {
                        "begin_snap_id": begin_snap_id,
                        "end_snap_id": end_snap_id,
                    },
                    "sql_text_sample": sql_text,
                    "generated_playbook": {
                        "spm_pin_block_sql": "\n".join(spm_block),
                        "verify_sql": verify_sql,
                        "targeted_purge_sql": purge_sql,
                    },
                    "notes": notes,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_apply_sql_plan_baseline_pin(
    sql_id: str,
    plan_hash_value: Optional[int] = None,
    lookback_days: int = 14,
    begin_snap_id: Optional[int] = None,
    end_snap_id: Optional[int] = None,
    purge_cursor: bool = True,
    confirm_apply: bool = False,
) -> str:
    """
    Apply plan-regression remediation by loading and fixing an SPM baseline for a SQL_ID.
    Safety gate: no DB change is made unless confirm_apply=true.
    """
    sql_id = _validate_sql_id(sql_id)

    conn = _connect()
    try:
        with conn.cursor() as cur:
            best_plan_sql = """
                with p as (
                    select
                        h.plan_hash_value,
                        sum(h.executions_delta) execs,
                        sum(h.elapsed_time_delta) elapsed_us
                    from dba_hist_sqlstat h
                    join dba_hist_snapshot sn
                      on sn.snap_id = h.snap_id
                     and sn.dbid = h.dbid
                     and sn.instance_number = h.instance_number
                    where h.sql_id = :sql_id
                      and sn.begin_interval_time >= systimestamp - numtodsinterval(:days, 'DAY')
                    group by h.plan_hash_value
                )
                select
                    plan_hash_value,
                    round(case when execs > 0 then (elapsed_us / execs) / 1000000 end, 6) sec_per_exec,
                    execs
                from p
                order by sec_per_exec nulls last, execs desc
                fetch first 1 rows only
            """

            chosen_plan_hash = plan_hash_value
            if chosen_plan_hash is None:
                cols, rows = _exec_query(
                    cur, best_plan_sql, {"sql_id": sql_id, "days": max(1, lookback_days)}
                )
                if not rows:
                    cache_best_sql = """
                        select plan_hash_value
                        from (
                            select
                                plan_hash_value,
                                round(case when sum(executions) > 0 then (sum(elapsed_time) / sum(executions)) / 1000000 end, 6) sec_per_exec,
                                sum(executions) execs
                            from gv$sql
                            where sql_id = :sql_id
                            group by plan_hash_value
                            order by sec_per_exec nulls last, execs desc
                        )
                        fetch first 1 rows only
                    """
                    cols, rows = _exec_query(cur, cache_best_sql, {"sql_id": sql_id})
                if not rows:
                    return JSONFormatter.format_response(
                        {
                            "sql_id": sql_id,
                            "error": "No plan found from AWR or cursor cache for requested SQL_ID.",
                            "lookback_days": lookback_days,
                        }
                    )
                chosen_plan_hash = int(_rows_dict(cols, rows)[0]["plan_hash_value"])

            if begin_snap_id is None or end_snap_id is None:
                snap_sql = """
                    select min(h.snap_id) begin_snap_id, max(h.snap_id) end_snap_id
                    from dba_hist_sqlstat h
                    join dba_hist_snapshot sn
                      on sn.snap_id = h.snap_id
                     and sn.dbid = h.dbid
                     and sn.instance_number = h.instance_number
                    where h.sql_id = :sql_id
                      and h.plan_hash_value = :phv
                      and sn.begin_interval_time >= systimestamp - numtodsinterval(:days, 'DAY')
                """
                s_cols, s_rows = _exec_query(
                    cur,
                    snap_sql,
                    {"sql_id": sql_id, "phv": int(chosen_plan_hash), "days": max(1, lookback_days)},
                )
                if s_rows:
                    snap = _rows_dict(s_cols, s_rows)[0]
                    begin_snap_id = begin_snap_id if begin_snap_id is not None else snap.get("begin_snap_id")
                    end_snap_id = end_snap_id if end_snap_id is not None else snap.get("end_snap_id")

            dry_run = {
                "sql_id": sql_id,
                "plan_hash_value": int(chosen_plan_hash),
                "begin_snap_id": begin_snap_id,
                "end_snap_id": end_snap_id,
                "steps": [
                    "Load fixed baseline from cursor cache for sql_id/plan_hash_value.",
                    "If not found in cache, load fixed baseline from AWR snapshot window.",
                    "Optionally purge only this SQL cursor from shared pool.",
                    "Verify baseline and subsequent cursor plan hash.",
                ],
                "generated_sql": {
                    "load_from_cursor_cache": (
                        "declare l_loaded number; begin "
                        f"l_loaded := dbms_spm.load_plans_from_cursor_cache(sql_id => '{sql_id}', "
                        f"plan_hash_value => {int(chosen_plan_hash)}, fixed => 'YES', enabled => 'YES'); "
                        "dbms_output.put_line(l_loaded); end; /"
                    ),
                    "load_from_awr_if_needed": (
                        None
                        if begin_snap_id is None or end_snap_id is None
                        else (
                            "declare l_loaded number; begin "
                            "l_loaded := dbms_spm.load_plans_from_awr("
                            f"begin_snap => {int(begin_snap_id)}, end_snap => {int(end_snap_id)}, "
                            f"basic_filter => q'[sql_id = ''{sql_id}'' and plan_hash_value = {int(chosen_plan_hash)}]', "
                            "fixed => 'YES', enabled => 'YES'); "
                            "dbms_output.put_line(l_loaded); end; /"
                        )
                    ),
                    "verify": (
                        "select b.sql_handle, b.plan_name, b.enabled, b.accepted, b.fixed "
                        "from dba_sql_plan_baselines b "
                        "join v$sqlarea v on v.exact_matching_signature = b.signature "
                        f"where v.sql_id = '{sql_id}';"
                    ),
                    "purge_cursor": (
                        "select address, hash_value from v$sqlarea "
                        f"where sql_id = '{sql_id}'; then exec dbms_shared_pool.purge('<ADDRESS>,<HASH_VALUE>', 'C');"
                    ),
                },
                "note": "Set confirm_apply=true to execute these steps.",
            }

            if not confirm_apply:
                return JSONFormatter.format_response(dry_run, optimize=True)

            load_from_cache = 0
            with conn.cursor() as work:
                loaded = work.callfunc(
                    "dbms_spm.load_plans_from_cursor_cache",
                    int,
                    [
                        sql_id,
                        int(chosen_plan_hash),
                        None,
                        None,
                        "YES",
                        "YES",
                    ],
                )
                load_from_cache = int(loaded or 0)

            load_from_awr = 0
            if load_from_cache == 0 and begin_snap_id is not None and end_snap_id is not None:
                with conn.cursor() as work:
                    loaded2 = work.callfunc(
                        "dbms_spm.load_plans_from_awr",
                        int,
                        [
                            int(begin_snap_id),
                            int(end_snap_id),
                            f"sql_id = '{sql_id}' and plan_hash_value = {int(chosen_plan_hash)}",
                            None,
                            "YES",
                            "YES",
                        ],
                    )
                    load_from_awr = int(loaded2 or 0)

            purge_results: List[Dict[str, Any]] = []
            if purge_cursor:
                addr_sql = "select address, hash_value from v$sqlarea where sql_id = :sql_id"
                a_cols, a_rows = _exec_query(cur, addr_sql, {"sql_id": sql_id})
                for row in _rows_dict(a_cols, a_rows):
                    address = row.get("address")
                    hash_value = row.get("hash_value")
                    if address and hash_value is not None:
                        name = f"{_format_cursor_address(address)},{hash_value}"
                        status = "ok"
                        error = None
                        try:
                            cur.callproc("dbms_shared_pool.purge", [name, "C"])
                        except Exception as e:
                            status = "failed"
                            error = str(e)
                        purge_results.append(
                            {
                                "name": name,
                                "status": status,
                                "error": error,
                            }
                        )

            verify_sql = """
                select b.sql_handle, b.plan_name, b.enabled, b.accepted, b.fixed
                from dba_sql_plan_baselines b
                join v$sqlarea v on v.exact_matching_signature = b.signature
                where v.sql_id = :sql_id
            """
            v_cols, v_rows = _exec_query(cur, verify_sql, {"sql_id": sql_id})

            conn.commit()
            return JSONFormatter.format_response(
                {
                    "applied": True,
                    "sql_id": sql_id,
                    "plan_hash_value": int(chosen_plan_hash),
                    "begin_snap_id": begin_snap_id,
                    "end_snap_id": end_snap_id,
                    "load_results": {
                        "from_cursor_cache": load_from_cache,
                        "from_awr": load_from_awr,
                    },
                    "purge_cursor_requested": purge_cursor,
                    "purge_results": purge_results,
                    "verified_baselines": _rows_dict(v_cols, v_rows),
                    "notes": [
                        "If no baseline rows are visible, ensure sql_id exists in cursor cache/sqlarea and user has DBA_SQL_PLAN_BASELINES access.",
                        "Baseline pinning (fixed=YES) is the primary mechanism to force old plan selection.",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_stats_health_check(owner: Optional[str] = None, top_n: int = 100) -> str:
    """
    Inspect table statistics health: stale stats, missing stats, locked stats.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            where = "where 1=1"
            binds: Dict[str, Any] = {"top_n": max(1, top_n)}
            if owner:
                where += " and owner = upper(:owner)"
                binds["owner"] = owner

            sql = f"""
                select
                    owner,
                    table_name,
                    stale_stats,
                    last_analyzed,
                    num_rows,
                    stattype_locked
                from dba_tab_statistics
                {where}
                order by
                    case when stale_stats = 'YES' then 0 else 1 end,
                    last_analyzed nulls first
                fetch first :top_n rows only
            """
            cols, rows = _exec_query(cur, sql, binds)
            data = _rows_dict(cols, rows)
            stale = [r for r in data if (r.get("stale_stats") or "").upper() == "YES"]
            missing = [r for r in data if r.get("last_analyzed") is None]
            locked = [r for r in data if r.get("stattype_locked") is not None]
            return JSONFormatter.format_response(
                {
                    "rows": data,
                    "summary": {
                        "stale_count": len(stale),
                        "missing_count": len(missing),
                        "locked_count": len(locked),
                    },
                    "recommendations": [
                        "Gather stale or missing table stats before forcing hints.",
                        "Review locked stats for intentional pinning vs stale drift.",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_index_advisor_lite(owner: Optional[str] = None, top_n: int = 50) -> str:
    """
    Provide lightweight index health/advisory signals (duplicate prefixes and poor clustering).
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            binds: Dict[str, Any] = {"top_n": max(1, top_n)}
            where = ""
            if owner:
                where = "where i.owner = upper(:owner)"
                binds["owner"] = owner

            sql = f"""
                select
                    i.owner,
                    i.table_name,
                    i.index_name,
                    i.blevel,
                    i.leaf_blocks,
                    i.clustering_factor,
                    t.num_rows
                from dba_indexes i
                left join dba_tables t
                  on t.owner = i.table_owner
                 and t.table_name = i.table_name
                {where}
                order by i.leaf_blocks desc nulls last
                fetch first :top_n rows only
            """
            dup_sql = f"""
                select
                    c.table_owner as owner,
                    c.table_name,
                    c.column_name,
                    count(*) index_count
                from dba_ind_columns c
                join dba_indexes i
                  on i.owner = c.index_owner
                 and i.index_name = c.index_name
                {"where c.table_owner = upper(:owner)" if owner else ""}
                  and c.column_position = 1
                group by c.table_owner, c.table_name, c.column_name
                having count(*) > 1
                order by index_count desc
                fetch first :top_n rows only
            """

            cols, rows = _exec_query(cur, sql, binds)
            dup_cols, dup_rows = _exec_query(cur, dup_sql, binds)
            index_rows = _rows_dict(cols, rows)
            high_cf = []
            for r in index_rows:
                cf = r.get("clustering_factor")
                nr = r.get("num_rows")
                if isinstance(cf, (int, float)) and isinstance(nr, (int, float)) and nr > 0 and cf > nr * 0.9:
                    high_cf.append(r)

            return JSONFormatter.format_response(
                {
                    "index_sample": index_rows,
                    "duplicate_leading_column_candidates": _rows_dict(dup_cols, dup_rows),
                    "high_clustering_factor_candidates": high_cf[:top_n],
                    "recommendations": [
                        "Validate duplicate-leading-column indexes for possible consolidation.",
                        "High clustering factor may indicate random I/O for range scans.",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_tablespace_capacity_forecast(days: int = 30) -> str:
    """
    Report current tablespace utilization and simple growth projection.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            current_sql = """
                with df as (
                    select tablespace_name, sum(bytes) bytes, sum(case when autoextensible = 'YES' then maxbytes else bytes end) maxbytes
                    from dba_data_files
                    group by tablespace_name
                ),
                fs as (
                    select tablespace_name, sum(bytes) free_bytes
                    from dba_free_space
                    group by tablespace_name
                )
                select
                    df.tablespace_name,
                    df.bytes allocated_bytes,
                    nvl(fs.free_bytes, 0) free_bytes,
                    df.bytes - nvl(fs.free_bytes, 0) used_bytes,
                    df.maxbytes
                from df
                left join fs on fs.tablespace_name = df.tablespace_name
                order by used_bytes desc
            """
            hist_sql = """
                select
                    t.tablespace_name,
                    sn.begin_interval_time sample_time,
                    (h.tablespace_size * t.block_size) tablespace_size_bytes,
                    (h.tablespace_usedsize * t.block_size) used_bytes
                from dba_hist_tbspc_space_usage h
                join dba_hist_snapshot sn
                  on sn.snap_id = h.snap_id
                 and sn.dbid = h.dbid
                 and sn.instance_number = h.instance_number
                join v$tablespace t
                  on t.ts# = h.tablespace_id
                where sn.begin_interval_time >= systimestamp - numtodsinterval(7, 'DAY')
                order by t.tablespace_name, sn.begin_interval_time
            """

            c_cols, c_rows = _exec_query(cur, current_sql)
            current = _rows_dict(c_cols, c_rows)

            projections: Dict[str, Dict[str, Any]] = {}
            try:
                h_cols, h_rows = _exec_query(cur, hist_sql)
                hist = _rows_dict(h_cols, h_rows)
                grouped: Dict[str, List[float]] = {}
                for r in hist:
                    ts = str(r.get("tablespace_name"))
                    grouped.setdefault(ts, []).append(float(r.get("used_bytes") or 0.0))

                for ts, series in grouped.items():
                    if len(series) < 2:
                        continue
                    deltas = [series[i] - series[i - 1] for i in range(1, len(series))]
                    growth_per_snap = mean(deltas) if deltas else 0.0
                    projections[ts] = {"avg_growth_per_snapshot_bytes": round(growth_per_snap, 2)}
            except Exception:
                projections = {}

            out = []
            for r in current:
                alloc = float(r.get("allocated_bytes") or 0.0)
                used = float(r.get("used_bytes") or 0.0)
                pct = round((used / alloc) * 100.0, 2) if alloc > 0 else None
                ts = str(r.get("tablespace_name"))
                proj = projections.get(ts, {})
                out.append(
                    {
                        **r,
                        "used_pct": pct,
                        "projection": proj,
                    }
                )

            return JSONFormatter.format_response(
                {
                    "forecast_days": days,
                    "tablespaces": out,
                    "recommendations": [
                        "Alert at 80/90/95% with separate thresholds for temp and undo.",
                        "Review autoextend maxbytes ceilings to avoid silent exhaustion.",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_session_leak_detector(idle_minutes: int = 30, top_n: int = 100) -> str:
    """
    Detect likely leaked/inactive sessions by module/program/machine patterns.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            sql = """
                select
                    nvl(username, 'UNKNOWN') username,
                    nvl(module, 'UNKNOWN') module,
                    nvl(program, 'UNKNOWN') program,
                    nvl(machine, 'UNKNOWN') machine,
                    count(*) session_count,
                    max(last_call_et) max_idle_sec,
                    round(avg(last_call_et), 2) avg_idle_sec
                from v$session
                where type = 'USER'
                  and status = 'INACTIVE'
                  and last_call_et >= :idle_sec
                group by username, module, program, machine
                order by session_count desc, max_idle_sec desc
                fetch first :top_n rows only
            """
            cols, rows = _exec_query(
                cur,
                sql,
                {"idle_sec": max(1, idle_minutes) * 60, "top_n": max(1, top_n)},
            )
            return JSONFormatter.format_response(
                {
                    "idle_minutes_threshold": idle_minutes,
                    "candidates": _rows_dict(cols, rows),
                    "recommendations": [
                        "Cross-check with app pool min/max settings and abandoned timeout.",
                        "Watch for high idle counts from same module+machine fingerprint.",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_parameter_change_audit(top_n: int = 200) -> str:
    """
    Audit current non-default/modified parameters and SPFILE overrides.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            mod_sql = """
                select
                    name,
                    value,
                    isdefault,
                    ismodified,
                    issys_modifiable,
                    isinstance_modifiable
                from v$system_parameter
                where isdefault = 'FALSE' or ismodified <> 'FALSE'
                order by name
                fetch first :top_n rows only
            """
            sp_sql = """
                select
                    name,
                    value,
                    display_value,
                    isspecified
                from v$spparameter
                where isspecified = 'TRUE'
                order by name
                fetch first :top_n rows only
            """
            mod_cols, mod_rows = _exec_query(cur, mod_sql, {"top_n": max(1, top_n)})
            sp_cols, sp_rows = _exec_query(cur, sp_sql, {"top_n": max(1, top_n)})

            risky = []
            risk_names = {
                "optimizer_features_enable",
                "cursor_sharing",
                "optimizer_mode",
                "parallel_degree_policy",
                "filesystemio_options",
                "db_file_multiblock_read_count",
            }
            for row in _rows_dict(mod_cols, mod_rows):
                if str(row.get("name", "")).lower() in risk_names:
                    risky.append(row)

            return JSONFormatter.format_response(
                {
                    "modified_parameters": _rows_dict(mod_cols, mod_rows),
                    "spfile_overrides": _rows_dict(sp_cols, sp_rows),
                    "high_impact_parameters": risky,
                    "recommendations": [
                        "Track parameter deltas alongside deployment and incident timelines.",
                        "Validate hidden coupling with optimizer and parallel settings before changes.",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_create_spm_baseline_from_source(
    sql_id: str,
    plan_hash_value: Optional[int] = None,
    source: str = "cursor",
    begin_snap_id: Optional[int] = None,
    end_snap_id: Optional[int] = None,
    fixed: bool = True,
    enabled: bool = True,
    confirm_apply: bool = False,
) -> str:
    """Create SQL Plan Baseline from cursor cache or AWR."""
    sql_id = _validate_sql_id(sql_id)
    src = source.lower().strip()
    if src not in {"cursor", "awr"}:
        raise ValueError("source must be 'cursor' or 'awr'")
    if src == "awr" and (begin_snap_id is None or end_snap_id is None):
        raise ValueError("begin_snap_id and end_snap_id are required when source='awr'")

    if not confirm_apply:
        return JSONFormatter.format_response(
            {
                "dry_run": True,
                "sql_id": sql_id,
                "plan_hash_value": plan_hash_value,
                "source": src,
                "begin_snap_id": begin_snap_id,
                "end_snap_id": end_snap_id,
                "fixed": fixed,
                "enabled": enabled,
                "next_step": "Set confirm_apply=true to create baseline.",
            },
            optimize=True,
        )

    conn = _connect()
    try:
        with conn.cursor() as cur:
            loaded = 0
            if src == "cursor":
                loaded = int(
                    cur.callfunc(
                        "dbms_spm.load_plans_from_cursor_cache",
                        int,
                        [
                            sql_id,
                            int(plan_hash_value) if plan_hash_value is not None else None,
                            None,
                            None,
                            "YES" if fixed else "NO",
                            "YES" if enabled else "NO",
                        ],
                    )
                    or 0
                )
            else:
                bf = f"sql_id = '{sql_id}'"
                if plan_hash_value is not None:
                    bf += f" and plan_hash_value = {int(plan_hash_value)}"
                loaded = int(
                    cur.callfunc(
                        "dbms_spm.load_plans_from_awr",
                        int,
                        [
                            int(begin_snap_id),
                            int(end_snap_id),
                            bf,
                            None,
                            "YES" if fixed else "NO",
                            "YES" if enabled else "NO",
                        ],
                    )
                    or 0
                )
            _execute_compat(cur, 
                """
                select b.sql_handle, b.plan_name, b.enabled, b.accepted, b.fixed
                from dba_sql_plan_baselines b
                join v$sqlarea v on v.exact_matching_signature = b.signature
                where v.sql_id = :sql_id
                """,
                {"sql_id": sql_id},
            )
            baselines = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            conn.commit()
            return JSONFormatter.format_response(
                {
                    "applied": True,
                    "loaded_count": loaded,
                    "sql_id": sql_id,
                    "source": src,
                    "baselines": baselines,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_spm_baseline_manager(
    action: str = "list",
    sql_handle: Optional[str] = None,
    plan_name: Optional[str] = None,
    top_n: int = 200,
    confirm_apply: bool = False,
) -> str:
    """Manage SQL Plan Baselines: list, enable, disable, fix, unfix, drop, evolve."""
    act = action.lower().strip()
    valid = {"list", "enable", "disable", "fix", "unfix", "drop", "evolve"}
    if act not in valid:
        raise ValueError(f"action must be one of: {sorted(valid)}")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if act == "list":
                where = "where 1=1"
                binds: Dict[str, Any] = {"top_n": max(1, top_n)}
                if sql_handle:
                    where += " and sql_handle = :sql_handle"
                    binds["sql_handle"] = sql_handle
                if plan_name:
                    where += " and plan_name = :plan_name"
                    binds["plan_name"] = plan_name
                _execute_compat(cur, 
                    f"""
                    select sql_handle, plan_name, enabled, accepted, fixed,
                           optimizer_cost, origin,
                           to_char(last_modified, 'YYYY-MM-DD HH24:MI:SS') last_modified
                    from dba_sql_plan_baselines
                    {where}
                    order by last_modified desc nulls last
                    fetch first :top_n rows only
                    """,
                    binds,
                )
                return JSONFormatter.format_response(
                    {"action": "list", "rows": _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())},
                    optimize=True,
                )

            if not sql_handle or not plan_name:
                raise ValueError("sql_handle and plan_name are required for non-list actions.")

            if not confirm_apply:
                return JSONFormatter.format_response(
                    {
                        "dry_run": True,
                        "action": act,
                        "sql_handle": sql_handle,
                        "plan_name": plan_name,
                        "next_step": "Set confirm_apply=true to execute.",
                    },
                    optimize=True,
                )

            result = None
            if act in {"enable", "disable", "fix", "unfix"}:
                attr = "ENABLED" if act in {"enable", "disable"} else "FIXED"
                val = "YES" if act in {"enable", "fix"} else "NO"
                result = cur.callfunc(
                    "dbms_spm.alter_sql_plan_baseline",
                    int,
                    [sql_handle, plan_name, attr, val],
                )
            elif act == "drop":
                result = cur.callfunc(
                    "dbms_spm.drop_sql_plan_baseline",
                    int,
                    [sql_handle, plan_name],
                )
            elif act == "evolve":
                _execute_compat(cur, 
                    "select dbms_spm.evolve_sql_plan_baseline(sql_handle => :h, plan_name => :p, verify => 'YES', commit => 'YES') from dual",
                    {"h": sql_handle, "p": plan_name},
                )
                result = cur.fetchone()[0]
            conn.commit()
            return JSONFormatter.format_response(
                {
                    "applied": True,
                    "action": act,
                    "sql_handle": sql_handle,
                    "plan_name": plan_name,
                    "result": str(result),
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_awr_sql_report_text(
    sql_id: str,
    begin_snap_id: int,
    end_snap_id: int,
    dbid: Optional[int] = None,
    instance_number: Optional[int] = None,
) -> str:
    """Generate AWR SQL report text for a SQL_ID and snapshot range."""
    sql_id = _validate_sql_id(sql_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if dbid is None:
                _execute_compat(cur, "select dbid from v$database")
                dbid = int(cur.fetchone()[0])
            if instance_number is None:
                _execute_compat(cur, "select instance_number from v$instance")
                instance_number = int(cur.fetchone()[0])
            params = {
                "dbid": dbid,
                "inst": instance_number,
                "begin_snap": int(begin_snap_id),
                "end_snap": int(end_snap_id),
                "sql_id": sql_id,
            }
            report_sql = """
                select output
                from table(
                    dbms_workload_repository.awr_sql_report_text(
                        :dbid, :inst, :begin_snap, :end_snap, :sql_id
                    )
                )
            """
            try:
                _execute_compat(cur, report_sql, params)
            except Exception as e:
                if "ORA-20020" not in str(e):
                    raise
                _execute_compat(cur, 
                    """
                    select dbid, instance_number
                    from dba_hist_snapshot
                    where snap_id in (:b, :e)
                    group by dbid, instance_number
                    having count(distinct snap_id) = 2
                    order by dbid, instance_number
                    """,
                    {"b": int(begin_snap_id), "e": int(end_snap_id)},
                )
                candidates = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
                if candidates:
                    cand = candidates[0]
                    params["dbid"] = int(cand["dbid"])
                    params["inst"] = int(cand["instance_number"])
                    _execute_compat(cur, report_sql, params)
                else:
                    return JSONFormatter.format_response(
                        {
                            "error": "Database/Instance/Snapshot mismatch and no matching snapshot context found.",
                            "sql_id": sql_id,
                            "begin_snap_id": int(begin_snap_id),
                            "end_snap_id": int(end_snap_id),
                            "requested_dbid": dbid,
                            "requested_instance_number": instance_number,
                        }
                    )
            lines = [str(r[0]) for r in cur.fetchall()]
            return JSONFormatter.format_response(
                {
                    "sql_id": sql_id,
                    "dbid": int(params["dbid"]),
                    "instance_number": int(params["inst"]),
                    "begin_snap_id": int(begin_snap_id),
                    "end_snap_id": int(end_snap_id),
                    "report_text": "".join(lines),
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_ash_report(
    window_minutes: int = 30,
    sql_id: Optional[str] = None,
    module: Optional[str] = None,
    machine: Optional[str] = None,
    top_n: int = 20,
) -> str:
    """ASH report with optional SQL/module/machine filters."""
    if window_minutes <= 0:
        raise ValueError("window_minutes must be > 0")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            filters = ["sample_time >= systimestamp - numtodsinterval(:mins, 'MINUTE')"]
            binds: Dict[str, Any] = {"mins": int(window_minutes), "top_n": max(1, top_n)}
            if sql_id:
                filters.append("sql_id = :sql_id")
                binds["sql_id"] = _validate_sql_id(sql_id)
            if module:
                filters.append("module = :module")
                binds["module"] = module
            if machine:
                filters.append("machine = :machine")
                binds["machine"] = machine
            where = " and ".join(filters)
            _execute_compat(cur, 
                f"""
                select wait_class, event, count(*) samples
                from v$active_session_history
                where {where}
                group by wait_class, event
                order by samples desc
                fetch first :top_n rows only
                """,
                binds,
            )
            waits = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                f"""
                select nvl(sql_id, 'UNKNOWN') sql_id, count(*) samples
                from v$active_session_history
                where {where}
                group by sql_id
                order by samples desc
                fetch first :top_n rows only
                """,
                binds,
            )
            top_sql = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                f"""
                select nvl(module, 'UNKNOWN') module, nvl(machine, 'UNKNOWN') machine, count(*) samples
                from v$active_session_history
                where {where}
                group by module, machine
                order by samples desc
                fetch first :top_n rows only
                """,
                binds,
            )
            top_dims = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            return JSONFormatter.format_response(
                {
                    "window_minutes": window_minutes,
                    "filters": {"sql_id": sql_id, "module": module, "machine": machine},
                    "top_waits": waits,
                    "top_sql": top_sql,
                    "top_module_machine": top_dims,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_lock_chain_analyzer(top_n: int = 50) -> str:
    """Analyze lock chains/blockers with suggested kill syntax."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _execute_compat(cur, 
                """
                select s.inst_id, s.sid, s.serial#, s.username, s.module, s.machine,
                       s.event, s.seconds_in_wait, s.blocking_instance, s.blocking_session, s.sql_id
                from gv$session s
                where s.type='USER' and s.blocking_session is not null
                order by s.seconds_in_wait desc
                fetch first :n rows only
                """,
                {"n": max(1, top_n)},
            )
            waiters = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select b.inst_id, b.sid, b.serial#, b.username, b.module, b.machine, b.sql_id, count(*) blocked_count
                from gv$session b
                join gv$session w
                  on w.blocking_instance = b.inst_id
                 and w.blocking_session = b.sid
                where w.blocking_session is not null
                group by b.inst_id, b.sid, b.serial#, b.username, b.module, b.machine, b.sql_id
                order by blocked_count desc
                fetch first :n rows only
                """,
                {"n": max(1, top_n)},
            )
            blockers = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            for b in blockers:
                b["kill_syntax"] = (
                    f"alter system kill session '{b.get('sid')},{b.get('serial#')},@{b.get('inst_id')}' immediate"
                )
            return JSONFormatter.format_response(
                {"waiters": waiters, "blockers": blockers},
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_bind_sensitivity_analyzer(sql_id: str, top_n: int = 100) -> str:
    """Analyze bind sensitivity/awareness, child cursor spread, and bind captures."""
    sql_id = _validate_sql_id(sql_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _execute_compat(cur, 
                """
                select child_number, plan_hash_value, is_bind_sensitive, is_bind_aware, is_shareable,
                       executions,
                       round(elapsed_time/nullif(executions,0)/1000000,6) elapsed_s_per_exec
                from v$sql
                where sql_id = :sql_id
                order by child_number
                """,
                {"sql_id": sql_id},
            )
            children = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select name, position, datatype_string,
                       count(distinct value_string) distinct_captured_values,
                       max(to_char(last_captured, 'YYYY-MM-DD HH24:MI:SS')) last_captured
                from v$sql_bind_capture
                where sql_id = :sql_id
                group by name, position, datatype_string
                order by position
                fetch first :n rows only
                """,
                {"sql_id": sql_id, "n": max(1, top_n)},
            )
            binds = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            bind_sensitive_children = sum(1 for c in children if (c.get("is_bind_sensitive") or "").upper() == "Y")
            bind_aware_children = sum(1 for c in children if (c.get("is_bind_aware") or "").upper() == "Y")
            return JSONFormatter.format_response(
                {
                    "sql_id": sql_id,
                    "child_cursors": children,
                    "bind_capture_summary": binds,
                    "summary": {
                        "child_count": len(children),
                        "bind_sensitive_children": bind_sensitive_children,
                        "bind_aware_children": bind_aware_children,
                    },
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_stats_drift_and_staleness_report(owner: Optional[str] = None, top_n: int = 200) -> str:
    """Report stale/missing/locked stats and table modification drift."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            where = "where 1=1"
            binds: Dict[str, Any] = {"n": max(1, top_n)}
            if owner:
                where += " and s.owner = upper(:owner)"
                binds["owner"] = owner
            _execute_compat(cur, 
                f"""
                select s.owner, s.table_name, s.stale_stats,
                       to_char(s.last_analyzed, 'YYYY-MM-DD HH24:MI:SS') last_analyzed,
                       s.num_rows, s.stattype_locked,
                       nvl(m.inserts,0) ins, nvl(m.updates,0) upd, nvl(m.deletes,0) del,
                       to_char(m.timestamp, 'YYYY-MM-DD HH24:MI:SS') mod_ts
                from dba_tab_statistics s
                left join dba_tab_modifications m
                  on m.table_owner = s.owner
                 and m.table_name = s.table_name
                {where}
                order by case when s.stale_stats='YES' then 0 else 1 end,
                         s.last_analyzed nulls first
                fetch first :n rows only
                """,
                binds,
            )
            rows = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            return JSONFormatter.format_response(
                {
                    "owner_filter": owner,
                    "rows": rows,
                    "summary": {
                        "stale_count": sum(1 for r in rows if (r.get("stale_stats") or "").upper() == "YES"),
                        "missing_count": sum(1 for r in rows if r.get("last_analyzed") is None),
                        "locked_count": sum(1 for r in rows if r.get("stattype_locked") is not None),
                    },
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_index_effectiveness_and_fk_gaps(owner: Optional[str] = None, top_n: int = 200) -> str:
    """Index effectiveness signals and foreign-key missing index gaps."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            where = "where 1=1"
            binds: Dict[str, Any] = {"n": max(1, top_n)}
            if owner:
                where += " and i.owner = upper(:owner)"
                binds["owner"] = owner
            _execute_compat(cur, 
                f"""
                select i.owner, i.table_name, i.index_name, i.blevel, i.leaf_blocks, i.clustering_factor, t.num_rows,
                       round(case when t.num_rows > 0 then i.clustering_factor / t.num_rows end, 4) cf_to_rows_ratio
                from dba_indexes i
                join dba_tables t on t.owner = i.table_owner and t.table_name = i.table_name
                {where}
                order by cf_to_rows_ratio desc nulls last
                fetch first :n rows only
                """,
                binds,
            )
            idx_rows = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())

            fk_where = ""
            if owner:
                fk_where = "and c.owner = upper(:owner)"
            _execute_compat(cur, 
                f"""
                with fk_cols as (
                    select c.owner, c.table_name, c.constraint_name,
                           listagg(cc.column_name, ',') within group(order by cc.position) fk_cols
                    from dba_constraints c
                    join dba_cons_columns cc
                      on cc.owner = c.owner
                     and cc.constraint_name = c.constraint_name
                    where c.constraint_type = 'R'
                    {fk_where}
                    group by c.owner, c.table_name, c.constraint_name
                ),
                idx_cols as (
                    select ic.table_owner owner, ic.table_name, ic.index_name,
                           listagg(ic.column_name, ',') within group(order by ic.column_position) idx_cols
                    from dba_ind_columns ic
                    group by ic.table_owner, ic.table_name, ic.index_name
                )
                select f.owner, f.table_name, f.constraint_name, f.fk_cols
                from fk_cols f
                where not exists (
                    select 1
                    from idx_cols i
                    where i.owner = f.owner
                      and i.table_name = f.table_name
                      and (i.idx_cols = f.fk_cols or i.idx_cols like f.fk_cols || ',%')
                )
                fetch first :n rows only
                """,
                binds,
            )
            fk_gaps = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())

            return JSONFormatter.format_response(
                {
                    "owner_filter": owner,
                    "index_effectiveness": idx_rows,
                    "missing_fk_indexes": fk_gaps,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_session_pressure_dashboard(top_n: int = 25) -> str:
    """Session pressure dashboard: sessions, open cursors, PGA consumers, active SQL concentration."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _execute_compat(cur, 
                """
                select status, count(*) sessions
                from v$session
                where type='USER'
                group by status
                order by sessions desc
                """
            )
            status_counts = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select nvl(s.module,'UNKNOWN') module, count(*) sessions
                from v$session s
                where s.type='USER'
                group by s.module
                order by sessions desc
                fetch first :n rows only
                """,
                {"n": max(1, top_n)},
            )
            top_modules = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select s.sid, s.serial#, nvl(s.username,'UNKNOWN') username, nvl(s.module,'UNKNOWN') module,
                       st.value opened_cursors_current
                from v$session s
                join v$sesstat st on st.sid = s.sid
                join v$statname sn on sn.statistic# = st.statistic#
                where sn.name = 'opened cursors current'
                order by st.value desc
                fetch first :n rows only
                """,
                {"n": max(1, top_n)},
            )
            top_cursors = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select s.sid, s.serial#, nvl(s.username,'UNKNOWN') username, nvl(s.module,'UNKNOWN') module,
                       round(st.value/1024/1024,2) session_pga_mb
                from v$session s
                join v$sesstat st on st.sid = s.sid
                join v$statname sn on sn.statistic# = st.statistic#
                where sn.name = 'session pga memory'
                order by st.value desc
                fetch first :n rows only
                """,
                {"n": max(1, top_n)},
            )
            top_pga = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select nvl(sql_id, 'UNKNOWN') sql_id, count(*) active_sessions
                from v$session
                where type='USER' and status='ACTIVE'
                group by sql_id
                order by active_sessions desc
                fetch first :n rows only
                """,
                {"n": max(1, top_n)},
            )
            active_sql = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())

            return JSONFormatter.format_response(
                {
                    "status_counts": status_counts,
                    "top_modules": top_modules,
                    "top_opened_cursors_sessions": top_cursors,
                    "top_pga_sessions": top_pga,
                    "active_sql_concentration": active_sql,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_sql_patch_quarantine(
    action: str = "list",
    sql_id: Optional[str] = None,
    patch_name: Optional[str] = None,
    hint_text: Optional[str] = None,
    description: Optional[str] = None,
    category: str = "DEFAULT",
    validate: bool = True,
    confirm_apply: bool = False,
) -> str:
    """List/create/drop SQL patches for emergency SQL behavior control."""
    act = action.lower().strip()
    if act not in {"list", "create", "drop"}:
        raise ValueError("action must be one of: list, create, drop")

    conn = _connect()
    try:
        with conn.cursor() as cur:
            if act == "list":
                where = "where 1=1"
                binds: Dict[str, Any] = {}
                if patch_name:
                    where += " and name = :name"
                    binds["name"] = patch_name
                _execute_compat(cur, 
                    f"""
                    select name, category, status, created, description
                    from dba_sql_patches
                    {where}
                    order by created desc
                    """,
                    binds,
                )
                rows = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
                return JSONFormatter.format_response({"action": "list", "rows": rows}, optimize=True)

            if not confirm_apply:
                return JSONFormatter.format_response(
                    {
                        "dry_run": True,
                        "action": act,
                        "sql_id": sql_id,
                        "patch_name": patch_name,
                        "next_step": "Set confirm_apply=true to execute.",
                    },
                    optimize=True,
                )

            if act == "create":
                if not sql_id:
                    raise ValueError("sql_id is required for create")
                sid = _validate_sql_id(sql_id)
                if not hint_text:
                    hint_text = "NO_PARALLEL"
                if not patch_name:
                    patch_name = f"mcp_patch_{sid}"
                _execute_compat(cur, 
                    """
                    select sql_fulltext
                    from v$sqlarea
                    where sql_id = :sql_id
                      and rownum = 1
                    """,
                    {"sql_id": sid},
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"SQL text not found in v$sqlarea for sql_id={sid}")
                sql_text = str(row[0])
                _execute_compat(cur, 
                    """
                    declare
                      v_name varchar2(128);
                    begin
                      v_name := dbms_sqldiag.create_sql_patch(
                        sql_text    => :sql_text,
                        hint_text   => :hint_text,
                        name        => :name,
                        description => :descr,
                        category    => :cat,
                        validate    => :val
                      );
                    end;
                    """,
                    {
                        "sql_text": sql_text,
                        "hint_text": hint_text,
                        "name": patch_name,
                        "descr": description or f"mcp patch for {sid}",
                        "cat": category,
                        "val": "TRUE" if validate else "FALSE",
                    },
                )
                conn.commit()
                return JSONFormatter.format_response(
                    {
                        "applied": True,
                        "action": "create",
                        "sql_id": sid,
                        "patch_name": patch_name,
                        "hint_text": hint_text,
                    },
                    optimize=True,
                )

            if act == "drop":
                if not patch_name:
                    raise ValueError("patch_name is required for drop")
                cur.callproc("dbms_sqldiag.drop_sql_patch", [patch_name])
                conn.commit()
                return JSONFormatter.format_response(
                    {"applied": True, "action": "drop", "patch_name": patch_name},
                    optimize=True,
                )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_short_window_activity_sample(
    window_seconds: int = 15,
    by: str = "sql_id",
    top_n: int = 20,
) -> str:
    """Short-window activity sample for active sessions and top dimensions."""
    dim = by.lower().strip()
    if dim not in {"sql_id", "sid", "module", "machine"}:
        raise ValueError("by must be one of: sql_id, sid, module, machine")
    secs = max(1, min(int(window_seconds), 120))
    conn = _connect()
    try:
        with conn.cursor() as cur:
            key_expr = {
                "sql_id": "nvl(sql_id,'UNKNOWN')",
                "sid": "to_char(session_id)",
                "module": "nvl(module,'UNKNOWN')",
                "machine": "nvl(machine,'UNKNOWN')",
            }[dim]
            _execute_compat(cur, 
                f"""
                select {key_expr} sample_key,
                       count(*) samples,
                       sum(case when session_state='ON CPU' then 1 else 0 end) on_cpu_samples,
                       sum(case when session_state='WAITING' then 1 else 0 end) wait_samples
                from v$active_session_history
                where sample_time >= systimestamp - numtodsinterval(:secs, 'SECOND')
                group by {key_expr}
                order by samples desc
                fetch first :top_n rows only
                """,
                {"secs": secs, "top_n": max(1, top_n)},
            )
            rows = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select wait_class, event, count(*) samples
                from v$active_session_history
                where sample_time >= systimestamp - numtodsinterval(:secs, 'SECOND')
                group by wait_class, event
                order by samples desc
                fetch first :top_n rows only
                """,
                {"secs": secs, "top_n": max(1, top_n)},
            )
            waits = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            return JSONFormatter.format_response(
                {
                    "window_seconds": secs,
                    "group_by": dim,
                    "top_samples": rows,
                    "top_waits": waits,
                    "note": "ASH sample-based quick triage similar to short-window ASH triage.",
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_cpu_pressure_analyzer(window_minutes: int = 15, top_n: int = 20) -> str:
    """Analyze CPU pressure from DB and host metrics with top SQL CPU consumers."""
    mins = max(1, min(int(window_minutes), 240))
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _execute_compat(cur, 
                """
                select metric_name, round(value, 3) value, metric_unit
                from v$sysmetric
                where group_id = 2
                  and metric_name in (
                    'Host CPU Utilization (%)',
                    'CPU Usage Per Sec',
                    'Database CPU Time Ratio',
                    'Average Active Sessions'
                  )
                order by metric_name
                """
            )
            metrics = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select stat_name, value
                from v$osstat
                where stat_name in ('NUM_CPUS', 'BUSY_TIME', 'IDLE_TIME', 'LOAD')
                """
            )
            osstat = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select sql_id,
                       plan_hash_value,
                       executions,
                       round(cpu_time / nullif(executions,0) / 1000000, 6) cpu_s_per_exec,
                       round(elapsed_time / nullif(executions,0) / 1000000, 6) elapsed_s_per_exec,
                       parsing_schema_name,
                       module
                from v$sql
                where executions > 0
                order by cpu_time desc
                fetch first :top_n rows only
                """,
                {"top_n": max(1, top_n)},
            )
            top_sql = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select wait_class, count(*) samples
                from v$active_session_history
                where sample_time >= systimestamp - numtodsinterval(:mins, 'MINUTE')
                group by wait_class
                order by samples desc
                """,
                {"mins": mins},
            )
            wait_mix = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            return JSONFormatter.format_response(
                {
                    "window_minutes": mins,
                    "sysmetrics": metrics,
                    "osstat": osstat,
                    "ash_wait_class_mix": wait_mix,
                    "top_sql_by_cpu": top_sql,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_latency_breakdown_report(window_minutes: int = 30, top_n: int = 30) -> str:
    """Latency and DB-time breakdown by ASH wait class/event and top SQL."""
    mins = max(1, min(int(window_minutes), 720))
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _execute_compat(cur, 
                """
                select wait_class, event, count(*) samples,
                       round(100 * ratio_to_report(count(*)) over (), 2) pct
                from v$active_session_history
                where sample_time >= systimestamp - numtodsinterval(:mins, 'MINUTE')
                group by wait_class, event
                order by samples desc
                fetch first :top_n rows only
                """,
                {"mins": mins, "top_n": max(1, top_n)},
            )
            waits = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select nvl(sql_id,'UNKNOWN') sql_id, count(*) samples,
                       round(100 * ratio_to_report(count(*)) over (), 2) pct
                from v$active_session_history
                where sample_time >= systimestamp - numtodsinterval(:mins, 'MINUTE')
                group by sql_id
                order by samples desc
                fetch first :top_n rows only
                """,
                {"mins": mins, "top_n": max(1, top_n)},
            )
            top_sql = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            return JSONFormatter.format_response(
                {
                    "window_minutes": mins,
                    "wait_event_breakdown": waits,
                    "top_sql_contributors": top_sql,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_memory_pressure_report(top_n: int = 25) -> str:
    """SGA/PGA pressure report with top memory consumers."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _execute_compat(cur, 
                """
                select name, round(bytes/1024/1024,2) mb
                from v$sgastat
                where bytes > 0
                order by bytes desc
                fetch first :n rows only
                """,
                {"n": max(1, top_n)},
            )
            sga_top = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select name, round(value/1024/1024,2) mb
                from v$pgastat
                where name in (
                    'aggregate PGA target parameter',
                    'total PGA allocated',
                    'total PGA inuse',
                    'over allocation count'
                )
                """
            )
            pga_stats = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select s.sid, s.serial#, nvl(s.username,'UNKNOWN') username, nvl(s.module,'UNKNOWN') module,
                       round(st.value/1024/1024,2) session_pga_mb
                from v$session s
                join v$sesstat st on st.sid = s.sid
                join v$statname sn on sn.statistic# = st.statistic#
                where sn.name = 'session pga memory'
                order by st.value desc
                fetch first :n rows only
                """,
                {"n": max(1, top_n)},
            )
            top_sessions = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select pool, name, round(bytes/1024/1024,2) mb
                from v$sgastat
                where lower(name) in ('free memory', 'kgh: no access')
                order by bytes desc
                """
            )
            free_signals = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            return JSONFormatter.format_response(
                {
                    "sga_top": sga_top,
                    "pga_stats": pga_stats,
                    "top_session_pga": top_sessions,
                    "sga_free_signals": free_signals,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_child_cursor_explosion_detector(min_children: int = 5, top_n: int = 50) -> str:
    """Detect SQL IDs with many child cursors and non-sharing reasons."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _execute_compat(cur, 
                """
                select sql_id, count(*) child_count,
                       count(distinct plan_hash_value) plan_count,
                       sum(executions) executions
                from v$sql
                group by sql_id
                having count(*) >= :min_children
                order by child_count desc, plan_count desc
                fetch first :n rows only
                """,
                {"min_children": max(2, int(min_children)), "n": max(1, top_n)},
            )
            heavy = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                select sql_id, child_number,
                       case when bind_mismatch='Y' then 1 else 0 end bind_mismatch,
                       case when optimizer_mismatch='Y' then 1 else 0 end optimizer_mismatch,
                       case when stats_row_mismatch='Y' then 1 else 0 end stats_row_mismatch,
                       case when language_mismatch='Y' then 1 else 0 end language_mismatch
                from v$sql_shared_cursor
                where sql_id in (
                    select sql_id
                    from (
                        select sql_id, count(*) child_count
                        from v$sql
                        group by sql_id
                        having count(*) >= :min_children
                        order by child_count desc
                    )
                    fetch first :n rows only
                )
                """,
                {"min_children": max(2, int(min_children)), "n": max(1, top_n)},
            )
            reasons = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            return JSONFormatter.format_response(
                {
                    "min_children": max(2, int(min_children)),
                    "top_sql_with_many_children": heavy,
                    "shared_cursor_reason_flags": reasons,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_sql_hotlist_manager(
    action: str = "list",
    sql_id: Optional[str] = None,
    severity: str = "medium",
    tags: Optional[List[str]] = None,
    note: Optional[str] = None,
    top_n: int = 20,
) -> str:
    """Manage local SQL hotlist for incident focus (list/add/remove/auto)."""
    act = action.lower().strip()
    if act not in {"list", "add", "remove", "auto"}:
        raise ValueError("action must be one of: list, add, remove, auto")
    data = _load_hotlist()
    items = data.get("items", [])

    if act == "add":
        if not sql_id:
            raise ValueError("sql_id is required for add")
        sid = _validate_sql_id(sql_id)
        items = [x for x in items if x.get("sql_id") != sid]
        items.append(
            {
                "sql_id": sid,
                "severity": severity.lower(),
                "tags": tags or [],
                "note": note,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        data["items"] = items
        _save_hotlist(data)
        return JSONFormatter.format_response({"action": "add", "item": items[-1]}, optimize=True)

    if act == "remove":
        if not sql_id:
            raise ValueError("sql_id is required for remove")
        sid = _validate_sql_id(sql_id)
        before = len(items)
        items = [x for x in items if x.get("sql_id") != sid]
        data["items"] = items
        _save_hotlist(data)
        return JSONFormatter.format_response(
            {"action": "remove", "sql_id": sid, "removed": before - len(items)},
            optimize=True,
        )

    if act == "auto":
        conn = _connect()
        try:
            with conn.cursor() as cur:
                _execute_compat(cur, 
                    """
                    select sql_id, count(*) samples
                    from v$active_session_history
                    where sample_time >= systimestamp - interval '30' minute
                      and sql_id is not null
                    group by sql_id
                    order by samples desc
                    fetch first :n rows only
                    """,
                    {"n": max(1, top_n)},
                )
                auto_rows = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
        finally:
            conn.close()
        now = datetime.now(timezone.utc).isoformat()
        for r in auto_rows:
            sid = str(r["sql_id"]).lower()
            if not re.fullmatch(r"[0-9a-z]{13}", sid):
                continue
            existing = next((x for x in items if x.get("sql_id") == sid), None)
            if existing:
                existing["updated_at"] = now
                existing["note"] = f"auto samples={r.get('samples')}"
            else:
                items.append(
                    {
                        "sql_id": sid,
                        "severity": "medium",
                        "tags": ["auto", "ash-top"],
                        "note": f"auto samples={r.get('samples')}",
                        "updated_at": now,
                    }
                )
        data["items"] = items
        _save_hotlist(data)
        return JSONFormatter.format_response({"action": "auto", "items": items[:top_n]}, optimize=True)

    items = sorted(items, key=lambda x: x.get("updated_at") or "", reverse=True)
    return JSONFormatter.format_response({"action": "list", "items": items[: max(1, top_n)]}, optimize=True)


@mcp.tool()
@trace_tool
async def oracle_parameter_timeline_diff(begin_snap_id: int, end_snap_id: int, top_n: int = 500) -> str:
    """Compare parameter values across two snapshots (timeline diff)."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _execute_compat(cur, 
                """
                with b as (
                    select parameter_name, value
                    from dba_hist_parameter
                    where snap_id = :b
                ),
                e as (
                    select parameter_name, value
                    from dba_hist_parameter
                    where snap_id = :e
                )
                select nvl(b.parameter_name, e.parameter_name) parameter_name,
                       b.value begin_value,
                       e.value end_value
                from b full outer join e
                  on b.parameter_name = e.parameter_name
                where nvl(b.value, '#NULL#') <> nvl(e.value, '#NULL#')
                order by parameter_name
                fetch first :n rows only
                """,
                {"b": int(begin_snap_id), "e": int(end_snap_id), "n": max(1, top_n)},
            )
            diffs = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            return JSONFormatter.format_response(
                {
                    "begin_snap_id": int(begin_snap_id),
                    "end_snap_id": int(end_snap_id),
                    "changed_parameters": diffs,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_alert_log_analyzer(window_minutes: int = 120, top_n: int = 100) -> str:
    """Analyze recent alert log entries (ORA errors and critical messages)."""
    mins = max(1, min(int(window_minutes), 1440))
    conn = _connect()
    try:
        with conn.cursor() as cur:
            try:
                _execute_compat(cur, 
                    """
                    select to_char(originating_timestamp, 'YYYY-MM-DD HH24:MI:SS') ts,
                           message_type,
                           message_level,
                           message_text
                    from v$diag_alert_ext
                    where originating_timestamp >= systimestamp - numtodsinterval(:mins, 'MINUTE')
                      and (
                        message_text like 'ORA-%'
                        or lower(message_text) like '%error%'
                        or lower(message_text) like '%critical%'
                      )
                    order by originating_timestamp desc
                    fetch first :n rows only
                    """,
                    {"mins": mins, "n": max(1, top_n)},
                )
                entries = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            except Exception as e:
                return JSONFormatter.format_response(
                    {
                        "window_minutes": mins,
                        "error": "Unable to query v$diag_alert_ext in current environment.",
                        "details": str(e),
                    }
                )

            counts: Dict[str, int] = {}
            for r in entries:
                txt = str(r.get("message_text") or "")
                m = re.search(r"(ORA-\d{5})", txt)
                key = m.group(1) if m else "OTHER"
                counts[key] = counts.get(key, 0) + 1
            top_errors = [{"error": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: x[1], reverse=True)]
            return JSONFormatter.format_response(
                {
                    "window_minutes": mins,
                    "top_error_codes": top_errors[:top_n],
                    "entries": entries,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_spm_baseline_pack_unpack(
    action: str = "list",
    table_name: str = "MCP_SPM_STGTAB",
    table_owner: Optional[str] = None,
    sql_handle: Optional[str] = None,
    enabled_only: bool = False,
    confirm_apply: bool = False,
) -> str:
    """Create/list/pack/unpack SPM baseline staging table using DBMS_SPM."""
    act = action.lower().strip()
    if act not in {"list", "create_stgtab", "pack", "unpack"}:
        raise ValueError("action must be one of: list, create_stgtab, pack, unpack")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if table_owner is None:
                _execute_compat(cur, "select user from dual")
                table_owner = str(cur.fetchone()[0])

            if act == "list":
                _execute_compat(cur, 
                    """
                    select owner, table_name
                    from all_tables
                    where owner = upper(:own)
                      and table_name = upper(:tab)
                    """,
                    {"own": table_owner, "tab": table_name},
                )
                exists = cur.fetchone() is not None
                rows = []
                if exists:
                    _execute_compat(cur, f"select count(*) from {table_owner}.{table_name}")
                    cnt = int(cur.fetchone()[0])
                    rows.append({"owner": table_owner.upper(), "table_name": table_name.upper(), "row_count": cnt})
                return JSONFormatter.format_response({"action": "list", "staging_tables": rows}, optimize=True)

            if not confirm_apply:
                return JSONFormatter.format_response(
                    {
                        "dry_run": True,
                        "action": act,
                        "table_owner": table_owner,
                        "table_name": table_name,
                        "sql_handle": sql_handle,
                        "enabled_only": enabled_only,
                        "next_step": "Set confirm_apply=true to execute.",
                    },
                    optimize=True,
                )

            if act == "create_stgtab":
                cur.callproc("dbms_spm.create_stgtab_baseline", [table_name.upper(), table_owner.upper(), None])
                conn.commit()
                return JSONFormatter.format_response(
                    {"applied": True, "action": act, "table_owner": table_owner.upper(), "table_name": table_name.upper()},
                    optimize=True,
                )

            if act == "pack":
                loaded = cur.callfunc(
                    "dbms_spm.pack_stgtab_baseline",
                    int,
                    [
                        table_name.upper(),
                        table_owner.upper(),
                        "%" if sql_handle is None else sql_handle,
                        "YES" if enabled_only else "NO",
                    ],
                )
                conn.commit()
                return JSONFormatter.format_response(
                    {"applied": True, "action": act, "packed": int(loaded or 0)},
                    optimize=True,
                )

            unpacked = cur.callfunc(
                "dbms_spm.unpack_stgtab_baseline",
                int,
                [table_name.upper(), table_owner.upper(), "YES", "YES"],
            )
            conn.commit()
            return JSONFormatter.format_response(
                {"applied": True, "action": act, "unpacked": int(unpacked or 0)},
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_sql_dependency_impact_map(sql_id: str, top_n: int = 200) -> str:
    """Map SQL dependency impact: objects referenced by plan with segment footprint."""
    sql_id = _validate_sql_id(sql_id)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _execute_compat(cur, 
                """
                with p as (
                    select distinct object_owner, object_name, object_type
                    from gv$sql_plan
                    where sql_id = :sql_id
                      and object_owner is not null
                      and object_name is not null
                )
                select p.object_owner, p.object_name, p.object_type,
                       round(nvl(sum(s.bytes),0)/1024/1024,2) segment_mb
                from p
                left join dba_segments s
                  on s.owner = p.object_owner
                 and s.segment_name = p.object_name
                group by p.object_owner, p.object_name, p.object_type
                order by segment_mb desc nulls last, p.object_owner, p.object_name
                fetch first :n rows only
                """,
                {"sql_id": sql_id, "n": max(1, top_n)},
            )
            objects = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            _execute_compat(cur, 
                """
                with objs as (
                    select distinct object_owner owner, object_name name
                    from gv$sql_plan
                    where sql_id = :sql_id
                      and object_owner is not null
                      and object_name is not null
                )
                select d.owner, d.name, d.type, d.referenced_owner, d.referenced_name, d.referenced_type
                from dba_dependencies d
                join objs o on o.owner = d.owner and o.name = d.name
                fetch first :n rows only
                """,
                {"sql_id": sql_id, "n": max(1, top_n)},
            )
            deps = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
            return JSONFormatter.format_response(
                {
                    "sql_id": sql_id,
                    "plan_referenced_objects": objects,
                    "dependency_edges": deps,
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_oem_long_running_queries(
    min_elapsed_seconds: int = 5,
    threshold_seconds: Optional[int] = None,
    window_minutes: int = 60,
    top_n: int = 50,
    only_active: bool = True,
) -> str:
    """
    OEM-style long-running SQL activity view using GV$SQL_MONITOR.
    Returns candidate SQLs plus heuristic tuning opportunities.
    """
    if threshold_seconds is not None:
        min_elapsed_seconds = int(threshold_seconds)
    min_s = max(1, int(min_elapsed_seconds))
    mins = max(1, int(window_minutes))
    conn = _connect()
    try:
        with conn.cursor() as cur:
            status_filter = "and m.status in ('EXECUTING','QUEUED')" if only_active else ""
            monitor_sql = f"""
                select
                    m.inst_id,
                    m.sid,
                    m.sql_id,
                    m.sql_exec_id,
                    m.status,
                    m.username,
                    nvl(m.module, 'UNKNOWN') module,
                    nvl(m.action, 'UNKNOWN') action,
                    round(m.elapsed_time/1000000, 3) elapsed_s,
                    round(m.cpu_time/1000000, 3) cpu_s,
                    m.buffer_gets,
                    m.disk_reads,
                    m.fetches,
                    m.px_servers_allocated,
                    substr(m.sql_text, 1, 1000) sql_text
                from gv$sql_monitor m
                where m.sql_id is not null
                  and m.elapsed_time >= :min_us
                  and m.last_refresh_time >= systimestamp - numtodsinterval(:mins, 'MINUTE')
                  {status_filter}
                order by m.elapsed_time desc
                fetch first :top_n rows only
            """
            _execute_compat(cur, 
                monitor_sql,
                {"min_us": min_s * 1_000_000, "mins": mins, "top_n": max(1, int(top_n))},
            )
            monitored = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())

            for row in monitored:
                sql_id = row.get("sql_id")
                opportunities: List[str] = []
                elapsed_s = float(row.get("elapsed_s") or 0)
                cpu_s = float(row.get("cpu_s") or 0)
                disk_reads = float(row.get("disk_reads") or 0)
                buffer_gets = float(row.get("buffer_gets") or 0)
                if elapsed_s > 0 and cpu_s > 0 and elapsed_s / cpu_s >= 2:
                    opportunities.append("Elapsed is much higher than CPU: check waits (I/O, locks, latches) before SQL rewrite.")
                if disk_reads > 10000:
                    opportunities.append("High physical I/O: validate index access/selectivity and storage latency.")
                if buffer_gets > 1000000:
                    opportunities.append("High buffer gets: review join cardinality, predicates, and unnecessary row visits.")
                try:
                    _execute_compat(cur, 
                        """
                        select plan_hash_value, executions,
                               round(elapsed_time/nullif(executions,0)/1000000,6) elapsed_s_per_exec,
                               round(cpu_time/nullif(executions,0)/1000000,6) cpu_s_per_exec
                        from gv$sql
                        where sql_id = :sql_id
                        order by last_active_time desc
                        fetch first 5 rows only
                        """,
                        {"sql_id": sql_id},
                    )
                    plans = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
                except Exception:
                    plans = []
                row["plan_candidates"] = plans
                if len({p.get("plan_hash_value") for p in plans if p.get("plan_hash_value") is not None}) > 1:
                    opportunities.append("Multiple plan hash values detected: evaluate plan regression and baseline/profile strategy.")
                if not opportunities:
                    opportunities.append("No immediate red flags; test with representative bind values and compare plans.")
                row["improvement_opportunities"] = opportunities

            return JSONFormatter.format_response(
                {
                    "filters": {
                        "min_elapsed_seconds": min_s,
                        "window_minutes": mins,
                        "only_active": bool(only_active),
                        "top_n": max(1, int(top_n)),
                    },
                    "long_running_queries": monitored,
                    "next_steps": [
                        "Use oracle_planx_sql_id(sql_id=...) for deep SQL_ID diagnostics.",
                        "Use oracle_test_query_with_binds(...) to A/B test original vs candidate SQL with real bind sets.",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_test_query_with_binds(
    original_sql: str,
    candidate_sql: Optional[str] = None,
    bind_sets: Optional[List[Dict[str, Any]]] = None,
    iterations: int = 3,
    fetch_rows: int = 200,
) -> str:
    """
    Benchmark original vs candidate read-only query using provided bind sets.
    Returns timing and detected plan hash/sql_id for each variant.
    """
    _ensure_read_only_sql(original_sql)
    if candidate_sql:
        _ensure_read_only_sql(candidate_sql)
    if bind_sets is None or len(bind_sets) == 0:
        bind_sets = [{}]
    iters = max(1, int(iterations))
    fetch_n = max(1, min(int(fetch_rows), 5000))

    conn = _connect()
    try:
        with conn.cursor() as cur:
            async def run_variant(label: str, sql_text: str) -> Dict[str, Any]:
                run_results: List[Dict[str, Any]] = []
                all_elapsed_ms: List[float] = []
                tag = f"MCP_BENCH_{label}_{int(time.time() * 1000)}"
                tagged_sql = f"/* {tag} */ {sql_text.strip()}"

                for idx, binds in enumerate(bind_sets or [{}], start=1):
                    per_iter_ms: List[float] = []
                    rows_seen = 0
                    for _ in range(iters):
                        t0 = time.perf_counter()
                        _execute_compat(cur, tagged_sql, binds or {})
                        fetched = 0
                        while fetched < fetch_n:
                            batch = cur.fetchmany(min(100, fetch_n - fetched))
                            if not batch:
                                break
                            fetched += len(batch)
                        rows_seen = max(rows_seen, fetched)
                        elapsed_ms = (time.perf_counter() - t0) * 1000.0
                        per_iter_ms.append(round(elapsed_ms, 3))
                        all_elapsed_ms.append(elapsed_ms)
                    run_results.append(
                        {
                            "bind_set_index": idx,
                            "binds": binds or {},
                            "rows_fetched_limited": rows_seen,
                            "iteration_elapsed_ms": per_iter_ms,
                            "avg_elapsed_ms": round(sum(per_iter_ms) / len(per_iter_ms), 3),
                        }
                    )

                _execute_compat(cur, 
                    """
                    select sql_id, plan_hash_value,
                           round(elapsed_time/nullif(executions,0)/1000000,6) elapsed_s_per_exec,
                           round(cpu_time/nullif(executions,0)/1000000,6) cpu_s_per_exec
                    from v$sql
                    where sql_text like :pat
                    order by last_active_time desc
                    fetch first 1 rows only
                    """,
                    {"pat": f"/* {tag} */%"},
                )
                row = cur.fetchone()
                sql_id = row[0] if row else None
                plan_hash = int(row[1]) if row and row[1] is not None else None
                perf = {
                    "sql_id": sql_id,
                    "plan_hash_value": plan_hash,
                    "elapsed_s_per_exec": row[2] if row else None,
                    "cpu_s_per_exec": row[3] if row else None,
                }

                plan_text = None
                if sql_id:
                    try:
                        _execute_compat(cur, 
                            """
                            select plan_table_output
                            from table(dbms_xplan.display_cursor(:sql_id, null, 'BASIC +OUTLINE'))
                            """,
                            {"sql_id": sql_id},
                        )
                        plan_text = "\n".join(str(r[0]) for r in cur.fetchall())
                    except Exception:
                        plan_text = None

                return {
                    "label": label,
                    "benchmark": run_results,
                    "overall_avg_elapsed_ms": round(sum(all_elapsed_ms) / len(all_elapsed_ms), 3) if all_elapsed_ms else None,
                    "cursor_perf": perf,
                    "plan_text": plan_text,
                }

            original_result = await run_variant("ORIGINAL", original_sql)
            candidate_result = await run_variant("CANDIDATE", candidate_sql) if candidate_sql else None

            comparison = None
            if candidate_result and original_result.get("overall_avg_elapsed_ms") and candidate_result.get("overall_avg_elapsed_ms"):
                o = float(original_result["overall_avg_elapsed_ms"])
                c = float(candidate_result["overall_avg_elapsed_ms"])
                improvement_pct = round(((o - c) / o) * 100.0, 2) if o > 0 else None
                comparison = {
                    "original_avg_ms": o,
                    "candidate_avg_ms": c,
                    "improvement_pct": improvement_pct,
                    "verdict": (
                        "candidate_better"
                        if improvement_pct is not None and improvement_pct > 5
                        else "no_clear_improvement"
                    ),
                }

            return JSONFormatter.format_response(
                {
                    "iterations": iters,
                    "fetch_rows_limit": fetch_n,
                    "bind_sets_count": len(bind_sets or []),
                    "original": original_result,
                    "candidate": candidate_result,
                    "comparison": comparison,
                    "guidance": [
                        "Use representative bind sets from production patterns before making app changes.",
                        "Validate row-count equivalence and correctness, not only speed.",
                        "If candidate is better and stable across binds, move to app-side rollout with guardrails.",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_sql_rewrite_benchmark_assistant(
    sql_id: Optional[str] = None,
    original_sql: Optional[str] = None,
    rewritten_sql: Optional[str] = None,
    use_captured_binds: bool = True,
    bind_sets: Optional[List[Dict[str, Any]]] = None,
    iterations: int = 3,
    fetch_rows: int = 200,
) -> str:
    """
    One-stop SQL tuning workflow:
    - identify SQL text from SQL_ID (or use provided SQL)
    - collect bind values from V$SQL_BIND_CAPTURE
    - suggest rewrite logic
    - benchmark original vs rewritten SQL (if provided)
    - show plan/perf differences
    """
    if not sql_id and not original_sql:
        raise ValueError("Provide at least sql_id or original_sql.")
    if sql_id:
        sql_id = _validate_sql_id(sql_id)

    iters = max(1, int(iterations))
    fetch_n = max(1, min(int(fetch_rows), 5000))

    conn = _connect()
    try:
        with conn.cursor() as cur:
            resolved_sql_id = sql_id
            if not original_sql:
                _execute_compat(cur, 
                    """
                    select sql_fulltext
                    from v$sqlarea
                    where sql_id = :sql_id
                      and rownum = 1
                    """,
                    {"sql_id": resolved_sql_id},
                )
                row = cur.fetchone()
                if row:
                    original_sql = str(row[0])
                else:
                    try:
                        _execute_compat(cur, 
                            """
                            select sql_text
                            from dba_hist_sqltext
                            where sql_id = :sql_id
                              and sql_text is not null
                              and rownum = 1
                            """,
                            {"sql_id": resolved_sql_id},
                        )
                        hrow = cur.fetchone()
                    except Exception:
                        hrow = None
                    if hrow:
                        original_sql = str(hrow[0])
                    else:
                        raise ValueError(
                            f"Could not find SQL text for sql_id={resolved_sql_id} in memory (V$SQLAREA) or AWR (DBA_HIST_SQLTEXT)."
                        )
            _ensure_read_only_sql(original_sql)
            if rewritten_sql:
                _ensure_read_only_sql(rewritten_sql)

            if not resolved_sql_id:
                try:
                    _execute_compat(cur, 
                        """
                        select sql_id
                        from v$sqlarea
                        where upper(regexp_replace(sql_text, '\\s+', ' ')) = :normalized
                        order by last_active_time desc
                        fetch first 1 row only
                        """,
                        {"normalized": _normalize_sql(original_sql)},
                    )
                    r = cur.fetchone()
                    resolved_sql_id = r[0] if r else None
                except Exception:
                    resolved_sql_id = None

            captured_binds: List[Dict[str, Any]] = []
            auto_bind_set: Dict[str, Any] = {}
            if use_captured_binds and resolved_sql_id:
                try:
                    _execute_compat(cur, 
                        """
                        select name, position, datatype_string, value_string,
                               to_char(last_captured, 'YYYY-MM-DD HH24:MI:SS') last_captured
                        from v$sql_bind_capture
                        where sql_id = :sql_id
                        order by last_captured desc nulls last, position
                        """,
                        {"sql_id": resolved_sql_id},
                    )
                    captured_binds = _rows_dict([d[0].lower() for d in cur.description], cur.fetchall())
                    for b in captured_binds:
                        nm = b.get("name")
                        if not nm:
                            nm = f"b{b.get('position')}"
                        key = str(nm).lstrip(":")
                        if key not in auto_bind_set and b.get("value_string") is not None:
                            auto_bind_set[key] = b.get("value_string")
                except Exception:
                    captured_binds = []
                    auto_bind_set = {}

            effective_bind_sets: List[Dict[str, Any]]
            if bind_sets and len(bind_sets) > 0:
                effective_bind_sets = bind_sets
            elif auto_bind_set:
                effective_bind_sets = [auto_bind_set]
            else:
                effective_bind_sets = [{}]

            rewrite_suggestions = _heuristic_sql_rewrite_suggestions(original_sql)

            async def run_variant(label: str, sql_text: str) -> Dict[str, Any]:
                tag = f"MCP_RW_BENCH_{label}_{int(time.time() * 1000)}"
                tagged_sql = f"/* {tag} */ {sql_text.strip()}"
                run_results: List[Dict[str, Any]] = []
                all_elapsed: List[float] = []

                for idx, binds in enumerate(effective_bind_sets, start=1):
                    per_iter: List[float] = []
                    errors: List[str] = []
                    rows_seen = 0
                    for _ in range(iters):
                        t0 = time.perf_counter()
                        try:
                            _execute_compat(cur, tagged_sql, binds or {})
                            fetched = 0
                            while fetched < fetch_n:
                                batch = cur.fetchmany(min(100, fetch_n - fetched))
                                if not batch:
                                    break
                                fetched += len(batch)
                            rows_seen = max(rows_seen, fetched)
                            elapsed_ms = (time.perf_counter() - t0) * 1000.0
                            per_iter.append(round(elapsed_ms, 3))
                            all_elapsed.append(elapsed_ms)
                        except Exception as e:
                            errors.append(str(e))
                    row = {
                        "bind_set_index": idx,
                        "binds": binds or {},
                        "rows_fetched_limited": rows_seen,
                        "iteration_elapsed_ms": per_iter,
                    }
                    if per_iter:
                        row["avg_elapsed_ms"] = round(sum(per_iter) / len(per_iter), 3)
                    if errors:
                        row["errors"] = errors
                    run_results.append(row)

                _execute_compat(cur, 
                    """
                    select sql_id, plan_hash_value,
                           round(elapsed_time/nullif(executions,0)/1000000,6) elapsed_s_per_exec,
                           round(cpu_time/nullif(executions,0)/1000000,6) cpu_s_per_exec
                    from v$sql
                    where sql_text like :pat
                    order by last_active_time desc
                    fetch first 1 rows only
                    """,
                    {"pat": f"/* {tag} */%"},
                )
                perf_row = cur.fetchone()
                perf = {
                    "sql_id": perf_row[0] if perf_row else None,
                    "plan_hash_value": int(perf_row[1]) if perf_row and perf_row[1] is not None else None,
                    "elapsed_s_per_exec": perf_row[2] if perf_row else None,
                    "cpu_s_per_exec": perf_row[3] if perf_row else None,
                }
                plan_text = None
                if perf.get("sql_id"):
                    try:
                        _execute_compat(cur, 
                            """
                            select plan_table_output
                            from table(dbms_xplan.display_cursor(:sql_id, null, 'BASIC +OUTLINE'))
                            """,
                            {"sql_id": perf["sql_id"]},
                        )
                        plan_text = "\n".join(str(r[0]) for r in cur.fetchall())
                    except Exception:
                        plan_text = None

                return {
                    "label": label,
                    "benchmark": run_results,
                    "overall_avg_elapsed_ms": round(sum(all_elapsed) / len(all_elapsed), 3) if all_elapsed else None,
                    "cursor_perf": perf,
                    "plan_text": plan_text,
                }

            original_result = await run_variant("ORIGINAL", original_sql)
            rewritten_result = await run_variant("REWRITTEN", rewritten_sql) if rewritten_sql else None

            comparison = None
            if rewritten_result and original_result.get("overall_avg_elapsed_ms") and rewritten_result.get("overall_avg_elapsed_ms"):
                o = float(original_result["overall_avg_elapsed_ms"])
                r = float(rewritten_result["overall_avg_elapsed_ms"])
                improvement_pct = round(((o - r) / o) * 100.0, 2) if o > 0 else None
                comparison = {
                    "original_avg_ms": o,
                    "rewritten_avg_ms": r,
                    "improvement_pct": improvement_pct,
                    "plan_hash_original": original_result["cursor_perf"].get("plan_hash_value"),
                    "plan_hash_rewritten": rewritten_result["cursor_perf"].get("plan_hash_value"),
                    "verdict": (
                        "rewritten_better"
                        if improvement_pct is not None and improvement_pct > 5
                        else "no_clear_improvement"
                    ),
                }

            return JSONFormatter.format_response(
                {
                    "resolved_sql_id": resolved_sql_id,
                    "use_captured_binds": bool(use_captured_binds),
                    "captured_binds": captured_binds,
                    "effective_bind_sets": effective_bind_sets,
                    "rewrite_suggestions": rewrite_suggestions,
                    "original": original_result,
                    "rewritten": rewritten_result,
                    "comparison": comparison,
                    "guidance": [
                        "If rewritten query is consistently better across bind sets, validate result correctness and roll out app changes gradually.",
                        "If plans diverge across binds, evaluate bind sensitivity and consider SPM/SQL Profile as interim controls.",
                        "Use this benchmark as directional; validate under representative production workload.",
                    ],
                },
                optimize=True,
            )
    finally:
        conn.close()


def _build_ash_time_predicate(
    start_time: Optional[str],
    end_time: Optional[str],
    window_minutes: int,
    alias: str = "a",
) -> Tuple[str, Dict[str, Any]]:
    binds: Dict[str, Any] = {}
    if start_time and end_time:
        binds["start_time"] = start_time
        binds["end_time"] = end_time
        return (
            f"{alias}.sample_time between to_timestamp(:start_time, 'YYYY-MM-DD HH24:MI:SS') "
            f"and to_timestamp(:end_time, 'YYYY-MM-DD HH24:MI:SS')",
            binds,
        )
    binds["window_minutes"] = max(1, window_minutes)
    return (
        f"{alias}.sample_time >= systimestamp - numtodsinterval(:window_minutes, 'MINUTE')",
        binds,
    )


def _pick_ash_source(source: str) -> Tuple[str, str]:
    normalized = (source or "auto").strip().lower()
    if normalized in {"v$ash", "vash", "memory", "live"}:
        return "gv$active_session_history", "a"
    if normalized in {"dba_hist_ash", "awr", "historical", "history"}:
        return "dba_hist_active_sess_history", "a"
    return "gv$active_session_history", "a"
@mcp.tool()
@trace_tool
async def oracle_ash_top_flexible(
    window_minutes: int = 60,
    top_n: int = 20,
    group_by: str = "event",
    source: str = "auto",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    sql_id: Optional[str] = None,
    module: Optional[str] = None,
    username: Optional[str] = None,
) -> str:
    """
    Flexible ASH top analysis inspired by ASH TOP patterns.
    group_by options: event, wait_class, sql_id, module, machine, program, session, plan_line
    """
    group_map = {
        "event": "nvl(a.event, 'ON CPU') as grp1",
        "wait_class": "nvl(a.wait_class, 'CPU') as grp1",
        "sql_id": "nvl(a.sql_id, 'NO_SQL_ID') as grp1",
        "module": "nvl(a.module, 'UNKNOWN') as grp1",
        "machine": "nvl(a.machine, 'UNKNOWN') as grp1",
        "program": "nvl(a.program, 'UNKNOWN') as grp1",
        "session": "to_char(a.session_id) || ',' || to_char(a.session_serial#) as grp1",
        "plan_line": "nvl(a.sql_plan_operation, 'UNKNOWN') || ' ' || nvl(a.sql_plan_options, '') || ' #' || nvl(to_char(a.sql_plan_line_id), '?') as grp1",
    }
    requested_group = (group_by or "event").strip().lower()
    if requested_group not in group_map:
        raise ValueError(f"group_by must be one of: {', '.join(sorted(group_map.keys()))}")

    source_view, alias = _pick_ash_source(source)
    time_predicate, binds = _build_ash_time_predicate(start_time, end_time, window_minutes, alias)
    where_clauses = [time_predicate]

    if sql_id:
        where_clauses.append(f"{alias}.sql_id = :sql_id")
        binds["sql_id"] = sql_id
    if module:
        where_clauses.append(f"upper(nvl({alias}.module, 'UNKNOWN')) like upper(:module)")
        binds["module"] = module
    if username:
        where_clauses.append(f"upper(nvl({alias}.user_id, -1)) in (select user_id from dba_users where username = upper(:username))")
        binds["username"] = username

    binds["top_n"] = max(1, top_n)
    group_expr = group_map[requested_group]
    sql = f"""
        select *
        from (
            select
                {group_expr},
                count(*) samples,
                round(100 * ratio_to_report(count(*)) over (), 2) pct
            from {source_view} {alias}
            where {' and '.join(where_clauses)}
            group by {group_expr.split(' as ')[0]}
            order by samples desc
        )
        where rownum <= :top_n
    """

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cols, rows = _exec_query(cur, sql, binds)
            return JSONFormatter.format_response(
                {
                    "source": source_view,
                    "group_by": requested_group,
                    "window_minutes": window_minutes,
                    "start_time": start_time,
                    "end_time": end_time,
                    "filters": {"sql_id": sql_id, "module": module, "username": username},
                    "top": _rows_dict(cols, rows),
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_session_delta_sampler(
    sid: Optional[int] = None,
    serial: Optional[int] = None,
    sql_id: Optional[str] = None,
    module: Optional[str] = None,
    sample_seconds: int = 5,
    samples: int = 3,
) -> str:
    """
    Lightweight session delta sampler inspired by session delta sampling patterns.
    """
    if sid is None and not sql_id and not module:
        raise ValueError("Provide at least one filter: sid, sql_id, or module")

    sample_seconds = max(1, sample_seconds)
    samples = max(2, samples)
    stats_of_interest = (
        "CPU used by this session",
        "session logical reads",
        "physical reads",
        "user commits",
        "user rollbacks",
        "parse count (hard)",
    )

    session_where = ["s.type = 'USER'"]
    binds: Dict[str, Any] = {}
    if sid is not None:
        session_where.append("s.sid = :sid")
        binds["sid"] = sid
    if serial is not None:
        session_where.append("s.serial# = :serial")
        binds["serial"] = serial
    if sql_id:
        session_where.append("s.sql_id = :sql_id")
        binds["sql_id"] = sql_id
    if module:
        session_where.append("upper(nvl(s.module, 'UNKNOWN')) like upper(:module)")
        binds["module"] = module

    capture_sql = f"""
        select
            s.inst_id,
            s.sid,
            s.serial#,
            nvl(s.username, 'UNKNOWN') username,
            nvl(s.module, 'UNKNOWN') module,
            nvl(s.program, 'UNKNOWN') program,
            nvl(s.sql_id, 'NO_SQL_ID') sql_id,
            sum(case when n.name = 'CPU used by this session' then st.value else 0 end) cpu_used,
            sum(case when n.name = 'session logical reads' then st.value else 0 end) logical_reads,
            sum(case when n.name = 'physical reads' then st.value else 0 end) physical_reads,
            sum(case when n.name = 'user commits' then st.value else 0 end) user_commits,
            sum(case when n.name = 'user rollbacks' then st.value else 0 end) user_rollbacks,
            sum(case when n.name = 'parse count (hard)' then st.value else 0 end) hard_parses
        from gv$session s
        join gv$sesstat st
          on st.inst_id = s.inst_id
         and st.sid = s.sid
        join gv$statname n
          on n.inst_id = st.inst_id
         and n.statistic# = st.statistic#
        where {' and '.join(session_where)}
          and n.name in ({", ".join([f"'{x}'" for x in stats_of_interest])})
        group by s.inst_id, s.sid, s.serial#, s.username, s.module, s.program, s.sql_id
        order by s.inst_id, s.sid, s.serial#
    """

    snapshots: List[Dict[str, Any]] = []
    conn = _connect()
    try:
        with conn.cursor() as cur:
            for idx in range(samples):
                cols, rows = _exec_query(cur, capture_sql, binds)
                snapshots.append(
                    {
                        "sample_index": idx + 1,
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                        "rows": _rows_dict(cols, rows),
                    }
                )
                if idx < samples - 1:
                    time.sleep(sample_seconds)
    finally:
        conn.close()

    deltas: List[Dict[str, Any]] = []
    for i in range(1, len(snapshots)):
        prev = {
            (r["inst_id"], r["sid"], r["serial#"]): r for r in snapshots[i - 1]["rows"]
        }
        curr = {
            (r["inst_id"], r["sid"], r["serial#"]): r for r in snapshots[i]["rows"]
        }
        keys = sorted(set(prev.keys()) & set(curr.keys()))
        for k in keys:
            p = prev[k]
            c = curr[k]
            deltas.append(
                {
                    "sample_window": f"{i}->{i+1}",
                    "inst_id": k[0],
                    "sid": k[1],
                    "serial#": k[2],
                    "username": c.get("username"),
                    "module": c.get("module"),
                    "sql_id": c.get("sql_id"),
                    "delta_cpu_used": (c.get("cpu_used") or 0) - (p.get("cpu_used") or 0),
                    "delta_logical_reads": (c.get("logical_reads") or 0) - (p.get("logical_reads") or 0),
                    "delta_physical_reads": (c.get("physical_reads") or 0) - (p.get("physical_reads") or 0),
                    "delta_user_commits": (c.get("user_commits") or 0) - (p.get("user_commits") or 0),
                    "delta_user_rollbacks": (c.get("user_rollbacks") or 0) - (p.get("user_rollbacks") or 0),
                    "delta_hard_parses": (c.get("hard_parses") or 0) - (p.get("hard_parses") or 0),
                }
            )

    return JSONFormatter.format_response(
        {
            "sample_seconds": sample_seconds,
            "samples": samples,
            "filters": {"sid": sid, "serial": serial, "sql_id": sql_id, "module": module},
            "snapshots": snapshots,
            "deltas": deltas,
        },
        optimize=True,
    )


@mcp.tool()
@trace_tool
async def oracle_wait_chain_analyzer(
    window_minutes: int = 30,
    top_n: int = 30,
    source: str = "auto",
) -> str:
    """
    Historical blocker->waiter chain analysis using ASH.
    """
    source_view, alias = _pick_ash_source(source)
    time_predicate, binds = _build_ash_time_predicate(None, None, window_minutes, alias)
    binds["top_n"] = max(1, top_n)
    sql = f"""
        select *
        from (
            select
                {alias}.inst_id waiter_inst_id,
                {alias}.session_id waiter_sid,
                {alias}.session_serial# waiter_serial,
                {alias}.blocking_inst_id blocker_inst_id,
                {alias}.blocking_session blocker_sid,
                nvl({alias}.event, 'ON CPU') event,
                nvl({alias}.sql_id, 'NO_SQL_ID') sql_id,
                count(*) samples
            from {source_view} {alias}
            where {time_predicate}
              and {alias}.blocking_session is not null
            group by
                {alias}.inst_id,
                {alias}.session_id,
                {alias}.session_serial#,
                {alias}.blocking_inst_id,
                {alias}.blocking_session,
                nvl({alias}.event, 'ON CPU'),
                nvl({alias}.sql_id, 'NO_SQL_ID')
            order by samples desc
        )
        where rownum <= :top_n
    """

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cols, rows = _exec_query(cur, sql, binds)
            return JSONFormatter.format_response(
                {
                    "source": source_view,
                    "window_minutes": window_minutes,
                    "chains": _rows_dict(cols, rows),
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_sql_monitor_like_analysis(
    sql_id: str,
    window_minutes: int = 60,
    source: str = "auto",
    top_n: int = 30,
) -> str:
    """
    SQL monitor-like plan line time attribution using ASH samples.
    """
    source_view, alias = _pick_ash_source(source)
    time_predicate, binds = _build_ash_time_predicate(None, None, window_minutes, alias)
    binds["sql_id"] = sql_id
    binds["top_n"] = max(1, top_n)

    line_sql = f"""
        select *
        from (
            select
                nvl({alias}.sql_plan_line_id, -1) sql_plan_line_id,
                nvl({alias}.sql_plan_operation, 'UNKNOWN') sql_plan_operation,
                nvl({alias}.sql_plan_options, ' ') sql_plan_options,
                nvl({alias}.event, 'ON CPU') event,
                count(*) samples,
                round(100 * ratio_to_report(count(*)) over (), 2) pct
            from {source_view} {alias}
            where {time_predicate}
              and {alias}.sql_id = :sql_id
            group by
                nvl({alias}.sql_plan_line_id, -1),
                nvl({alias}.sql_plan_operation, 'UNKNOWN'),
                nvl({alias}.sql_plan_options, ' '),
                nvl({alias}.event, 'ON CPU')
            order by samples desc
        )
        where rownum <= :top_n
    """
    summary_sql = """
        select
            sql_id,
            plan_hash_value,
            executions,
            elapsed_time,
            cpu_time,
            buffer_gets,
            disk_reads
        from (
            select
                sql_id,
                plan_hash_value,
                executions,
                elapsed_time,
                cpu_time,
                buffer_gets,
                disk_reads,
                last_active_time
            from v$sql
            where sql_id = :sql_id
            order by last_active_time desc
        )
        where rownum <= 1
    """

    conn = _connect()
    try:
        with conn.cursor() as cur:
            line_cols, line_rows = _exec_query(cur, line_sql, binds)
            sum_cols, sum_rows = _exec_query(cur, summary_sql, {"sql_id": sql_id})
            return JSONFormatter.format_response(
                {
                    "sql_id": sql_id,
                    "source": source_view,
                    "window_minutes": window_minutes,
                    "sql_summary": _rows_dict(sum_cols, sum_rows),
                    "plan_line_hotspots": _rows_dict(line_cols, line_rows),
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_latch_mutex_hotspots(
    window_minutes: int = 60,
    top_n: int = 20,
    source: str = "auto",
) -> str:
    """
    Latch/mutex contention hotspots inspired by latch/mutex profiling patterns.
    """
    source_view, alias = _pick_ash_source(source)
    time_predicate, binds = _build_ash_time_predicate(None, None, window_minutes, alias)
    binds["top_n"] = max(1, top_n)
    sql = f"""
        select *
        from (
            select
                nvl({alias}.event, 'ON CPU') event,
                nvl({alias}.wait_class, 'CPU') wait_class,
                nvl({alias}.sql_id, 'NO_SQL_ID') sql_id,
                nvl({alias}.module, 'UNKNOWN') module,
                count(*) samples
            from {source_view} {alias}
            where {time_predicate}
              and (
                    lower(nvl({alias}.event, '')) like 'latch:%'
                 or lower(nvl({alias}.event, '')) like '%mutex%'
                 or lower(nvl({alias}.event, '')) like 'library cache:%'
                 or lower(nvl({alias}.event, '')) like 'cursor:%'
              )
            group by
                nvl({alias}.event, 'ON CPU'),
                nvl({alias}.wait_class, 'CPU'),
                nvl({alias}.sql_id, 'NO_SQL_ID'),
                nvl({alias}.module, 'UNKNOWN')
            order by samples desc
        )
        where rownum <= :top_n
    """

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cols, rows = _exec_query(cur, sql, binds)
            return JSONFormatter.format_response(
                {
                    "source": source_view,
                    "window_minutes": window_minutes,
                    "hotspots": _rows_dict(cols, rows),
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_top_segments_by_stat(
    window_minutes: int = 60,
    top_n: int = 20,
    source: str = "auto",
    metric: str = "samples",
) -> str:
    """
    Top segments by ASH-derived activity metric (top segment stat patterns).
    metric options: samples, cpu_samples, io_samples, concurrency_samples
    """
    metric_map = {
        "samples": "count(*)",
        "cpu_samples": "sum(case when a.session_state = 'ON CPU' then 1 else 0 end)",
        "io_samples": "sum(case when lower(nvl(a.wait_class, '')) = 'user i/o' then 1 else 0 end)",
        "concurrency_samples": "sum(case when lower(nvl(a.wait_class, '')) = 'concurrency' then 1 else 0 end)",
    }
    metric_name = (metric or "samples").strip().lower()
    if metric_name not in metric_map:
        raise ValueError(f"metric must be one of: {', '.join(sorted(metric_map.keys()))}")

    source_view, alias = _pick_ash_source(source)
    time_predicate, binds = _build_ash_time_predicate(None, None, window_minutes, alias)
    binds["top_n"] = max(1, top_n)
    metric_expr = metric_map[metric_name].replace("a.", f"{alias}.")
    sql = f"""
        select *
        from (
            select
                nvl(o.owner, 'UNKNOWN') owner,
                nvl(o.object_name, 'OBJ#' || to_char({alias}.current_obj#)) object_name,
                nvl(o.subobject_name, '-') subobject_name,
                nvl(o.object_type, 'UNKNOWN') object_type,
                {metric_expr} metric_value
            from {source_view} {alias}
            left join dba_objects o
              on o.object_id = {alias}.current_obj#
            where {time_predicate}
              and {alias}.current_obj# is not null
              and {alias}.current_obj# > 0
            group by
                nvl(o.owner, 'UNKNOWN'),
                nvl(o.object_name, 'OBJ#' || to_char({alias}.current_obj#)),
                nvl(o.subobject_name, '-'),
                nvl(o.object_type, 'UNKNOWN')
            order by metric_value desc
        )
        where rownum <= :top_n
    """

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cols, rows = _exec_query(cur, sql, binds)
            return JSONFormatter.format_response(
                {
                    "source": source_view,
                    "window_minutes": window_minutes,
                    "metric": metric_name,
                    "top_segments": _rows_dict(cols, rows),
                },
                optimize=True,
            )
    finally:
        conn.close()


@mcp.tool()
@trace_tool
async def oracle_dbre_help_catalog(topic: Optional[str] = None) -> str:
    """
    Built-in runbook that maps common Oracle symptoms to recommended MCP tools.
    """
    catalog = [
        {
            "symptom": "Top waits / slowdown now",
            "tools": ["oracle_ash_top_flexible", "oracle_waits_hotspots", "oracle_sql_monitor_like_analysis"],
            "prompt": "Show top waits for last 30 minutes grouped by event and module.",
        },
        {
            "symptom": "Blocking and lock chains",
            "tools": ["oracle_blocking_sessions_analyzer", "oracle_wait_chain_analyzer"],
            "prompt": "Find blocker/waiter chains for last 30 minutes and rank by samples.",
        },
        {
            "symptom": "SQL got slower after plan flip",
            "tools": ["oracle_sql_plan_regression_detector", "oracle_sql_monitor_like_analysis", "oracle_generate_bind_query_from_vsql"],
            "prompt": "Find regressed SQL_IDs and show plan-line hotspots for the worst one.",
        },
        {
            "symptom": "Latch / mutex contention",
            "tools": ["oracle_latch_mutex_hotspots", "oracle_waits_hotspots"],
            "prompt": "List latch/mutex hotspots in last 60 minutes and top SQL_ID contributors.",
        },
        {
            "symptom": "Object-level hotspots",
            "tools": ["oracle_top_segments_by_stat", "oracle_index_advisor_lite", "oracle_stats_health_check"],
            "prompt": "Show top segments by IO samples and suggest index/stats actions.",
        },
        {
            "symptom": "RAC gc issues",
            "tools": ["oracle_rac_gc_hotspots", "oracle_ash_top_flexible"],
            "prompt": "Show RAC gc hotspots by instance/event/sql_id for last 30 minutes.",
        },
        {
            "symptom": "Session leak / pool issue",
            "tools": ["oracle_session_leak_detector", "oracle_session_delta_sampler"],
            "prompt": "Find idle leak candidates and sample one SID for delta activity.",
        },
    ]

    if topic and topic.strip():
        t = topic.strip().lower()
        filtered = [
            c for c in catalog
            if t in c["symptom"].lower()
            or any(t in x.lower() for x in c["tools"])
            or t in c["prompt"].lower()
        ]
    else:
        filtered = catalog

    return JSONFormatter.format_response(
        {
            "topic": topic,
            "entries": filtered,
            "tips": [
                "Use specific windows (e.g., last 30 minutes) during incidents.",
                "For SQL tuning, combine SQL_ID + bind capture + plan-line hotspot analysis.",
            ],
        },
        optimize=True,
    )


@mcp.tool()
@trace_tool
async def oracle_rac_gc_hotspots(
    window_minutes: int = 60,
    top_n: int = 30,
    source: str = "auto",
) -> str:
    """
    RAC Global Cache hotspot analysis from ASH (gc* events by instance/sql/object).
    """
    source_view, alias = _pick_ash_source(source)
    time_predicate, binds = _build_ash_time_predicate(None, None, window_minutes, alias)
    binds["top_n"] = max(1, top_n)
    sql = f"""
        select *
        from (
            select
                {alias}.inst_id,
                nvl({alias}.event, 'UNKNOWN') event,
                nvl({alias}.sql_id, 'NO_SQL_ID') sql_id,
                nvl(o.owner, 'UNKNOWN') owner,
                nvl(o.object_name, 'OBJ#' || to_char({alias}.current_obj#)) object_name,
                count(*) samples
            from {source_view} {alias}
            left join dba_objects o
              on o.object_id = {alias}.current_obj#
            where {time_predicate}
              and lower(nvl({alias}.event, '')) like 'gc%'
            group by
                {alias}.inst_id,
                nvl({alias}.event, 'UNKNOWN'),
                nvl({alias}.sql_id, 'NO_SQL_ID'),
                nvl(o.owner, 'UNKNOWN'),
                nvl(o.object_name, 'OBJ#' || to_char({alias}.current_obj#))
            order by samples desc
        )
        where rownum <= :top_n
    """

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cols, rows = _exec_query(cur, sql, binds)
            data = _rows_dict(cols, rows)
            inst_summary: Dict[str, int] = {}
            for row in data:
                inst_key = str(row.get("inst_id"))
                inst_summary[inst_key] = inst_summary.get(inst_key, 0) + int(row.get("samples") or 0)
            return JSONFormatter.format_response(
                {
                    "source": source_view,
                    "window_minutes": window_minutes,
                    "gc_hotspots": data,
                    "samples_by_instance": inst_summary,
                },
                optimize=True,
            )
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        if args.transport == "stdio":
            mcp.run(transport=args.transport, show_banner=False, log_level="ERROR")
        else:
            mcp.run(
                transport=args.transport,
                port=args.port,
                show_banner=False,
                log_level="ERROR",
            )
    except Exception as e:
        logger.error(f"error starting oracledb mcp server: {e}")
        print(f"Error starting OracleDB MCP server: {e}", file=sys.stderr)
        sys.exit(1)
