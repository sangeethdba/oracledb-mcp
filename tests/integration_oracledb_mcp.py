#!/usr/bin/env python3
"""Live integration runner for key OracleDB MCP tools in this repo."""

import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Tuple

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)

import oracledb_mcp  # noqa: E402


async def run_tool(tool, args: Dict[str, Any]) -> str:
    result = await tool.run(args)
    if getattr(result, "structured_content", None) and "result" in result.structured_content:
        return result.structured_content["result"]
    if getattr(result, "content", None):
        return result.content[0].text
    return "{}"


def require_env() -> None:
    missing = [k for k in ["ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_DSN"] if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


async def run() -> int:
    require_env()

    tests: List[Tuple[str, Any, Dict[str, Any]]] = [
        ("oracle_health_check", oracledb_mcp.oracle_health_check, {}),
        ("oracle_execute_readonly_query", oracledb_mcp.oracle_execute_readonly_query, {"sql": "select sysdate as now_dt from dual"}),
        ("oracle_waits_hotspots", oracledb_mcp.oracle_waits_hotspots, {"hours": 1, "top_n": 10}),
        ("oracle_sql_plan_regression_detector", oracledb_mcp.oracle_sql_plan_regression_detector, {"days": 7, "top_n": 10}),
        ("oracle_oem_long_running_queries", oracledb_mcp.oracle_oem_long_running_queries, {"min_elapsed_seconds": 5, "window_minutes": 60, "top_n": 10}),
        ("oracle_ash_top_flexible", oracledb_mcp.oracle_ash_top_flexible, {"window_minutes": 10, "group_by": "event", "top_n": 10}),
        ("oracle_session_delta_sampler", oracledb_mcp.oracle_session_delta_sampler, {"module": "%", "sample_seconds": 1, "samples": 2}),
        ("oracle_wait_chain_analyzer", oracledb_mcp.oracle_wait_chain_analyzer, {"window_minutes": 10, "top_n": 10}),
        ("oracle_sql_monitor_like_analysis", oracledb_mcp.oracle_sql_monitor_like_analysis, {"sql_id": "8f6t6uk2y6fht", "window_minutes": 10, "top_n": 10}),
        ("oracle_latch_mutex_hotspots", oracledb_mcp.oracle_latch_mutex_hotspots, {"window_minutes": 10, "top_n": 10}),
        ("oracle_top_segments_by_stat", oracledb_mcp.oracle_top_segments_by_stat, {"window_minutes": 10, "metric": "samples", "top_n": 10}),
        ("oracle_dbre_help_catalog", oracledb_mcp.oracle_dbre_help_catalog, {"topic": "wait"}),
        ("oracle_rac_gc_hotspots", oracledb_mcp.oracle_rac_gc_hotspots, {"window_minutes": 10, "top_n": 10}),
    ]

    results: List[Dict[str, Any]] = []
    for name, tool, args in tests:
        try:
            out = await run_tool(tool, args)
            json.loads(out)
            print(f"PASS {name}")
            results.append({"tool": name, "status": "PASS"})
        except Exception as e:
            print(f"FAIL {name}: {e}")
            results.append({"tool": name, "status": "FAIL", "error": str(e)})

    failed = [r for r in results if r["status"] == "FAIL"]
    print(json.dumps({"total": len(results), "failed": len(failed), "results": results}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))

#!/usr/bin/env python3
"""Registration test for OracleDB MCP tools in this repo."""

import os
import sys

os.environ.setdefault("ORACLE_USER", "test_user")
os.environ.setdefault("ORACLE_PASSWORD", "test_password")
os.environ.setdefault("ORACLE_DSN", "localhost:1521/XEPDB1")

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)


def test_tool_registration():
    import oracledb_mcp

    names = sorted(oracledb_mcp.mcp._tool_manager._tools.keys())

    required = {
        "oracle_health_check",
        "oracle_execute_readonly_query",
        "oracle_sql_rewrite_benchmark_assistant",
        "oracle_oem_long_running_queries",
        "oracle_ash_top_flexible",
        "oracle_session_delta_sampler",
        "oracle_wait_chain_analyzer",
        "oracle_sql_monitor_like_analysis",
        "oracle_latch_mutex_hotspots",
        "oracle_top_segments_by_stat",
        "oracle_dbre_help_catalog",
        "oracle_rac_gc_hotspots",
    }

    missing = sorted(required - set(names))
    assert not missing, f"Missing required tools: {missing}"
    assert len(names) >= 50, f"Unexpectedly low tool count: {len(names)}"
