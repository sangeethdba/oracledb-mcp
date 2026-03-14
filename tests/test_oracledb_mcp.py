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
