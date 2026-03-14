# OracleDB MCP Tool Catalog

Total tools: **54**

Call format in MCP clients:
- `tool_name(param=value, ...)`
- Use named arguments exactly as shown in each signature.

## 1. `oracle_alert_log_analyzer`
- Signature: `oracle_alert_log_analyzer(window_minutes: int = 120, top_n: int = 100) -> str`
- Purpose: Analyze recent alert log entries (ORA errors and critical messages).
- Example call:
```text
oracle_alert_log_analyzer(window_minutes=120, top_n=100)
```

## 2. `oracle_analyze_awr_report`
- Signature: `oracle_analyze_awr_report(report_text: Optional[str] = None, report_path: Optional[str] = None, begin_snap_id: Optional[int] = None, end_snap_id: Optional[int] = None, window_minutes: Optional[int] = None, window_hours: Optional[int] = None, dbid: Optional[int] = None, instance_number: Optional[int] = None) -> str`
- Purpose: Analyze AWR in one call:
- Example call:
```text
oracle_analyze_awr_report(report_text=None, report_path=None, begin_snap_id=None, end_snap_id=None, window_minutes=None, window_hours=None, dbid=None, instance_number=None)
```

## 3. `oracle_apply_sql_plan_baseline_pin`
- Signature: `oracle_apply_sql_plan_baseline_pin(sql_id: str, plan_hash_value: Optional[int] = None, lookback_days: int = 14, begin_snap_id: Optional[int] = None, end_snap_id: Optional[int] = None, purge_cursor: bool = True, confirm_apply: bool = False) -> str`
- Purpose: Apply plan-regression remediation by loading and fixing an SPM baseline for a SQL_ID.
- Example call:
```text
oracle_apply_sql_plan_baseline_pin(sql_id="<value>", plan_hash_value=None, lookback_days=14, begin_snap_id=None, end_snap_id=None, purge_cursor=True, confirm_apply=False)
```

## 4. `oracle_ash_report`
- Signature: `oracle_ash_report(window_minutes: int = 30, sql_id: Optional[str] = None, module: Optional[str] = None, machine: Optional[str] = None, top_n: int = 20) -> str`
- Purpose: ASH report with optional SQL/module/machine filters.
- Example call:
```text
oracle_ash_report(window_minutes=30, sql_id=None, module=None, machine=None, top_n=20)
```

## 5. `oracle_ash_top_flexible`
- Signature: `oracle_ash_top_flexible(window_minutes: int = 60, top_n: int = 20, group_by: str = 'event', source: str = 'auto', start_time: Optional[str] = None, end_time: Optional[str] = None, sql_id: Optional[str] = None, module: Optional[str] = None, username: Optional[str] = None) -> str`
- Purpose: Flexible ASH top analysis inspired by ASH TOP patterns.
- Example call:
```text
oracle_ash_top_flexible(window_minutes=60, top_n=20, group_by="event", source="auto", start_time=None, end_time=None, sql_id=None, module=None, username=None)
```

## 6. `oracle_awr_sql_report_text`
- Signature: `oracle_awr_sql_report_text(sql_id: str, begin_snap_id: int, end_snap_id: int, dbid: Optional[int] = None, instance_number: Optional[int] = None) -> str`
- Purpose: Generate AWR SQL report text for a SQL_ID and snapshot range.
- Example call:
```text
oracle_awr_sql_report_text(sql_id="<value>", begin_snap_id=1, end_snap_id=1, dbid=None, instance_number=None)
```

## 7. `oracle_bind_sensitivity_analyzer`
- Signature: `oracle_bind_sensitivity_analyzer(sql_id: str, top_n: int = 100) -> str`
- Purpose: Analyze bind sensitivity/awareness, child cursor spread, and bind captures.
- Example call:
```text
oracle_bind_sensitivity_analyzer(sql_id="<value>", top_n=100)
```

## 8. `oracle_blocking_sessions_analyzer`
- Signature: `oracle_blocking_sessions_analyzer(top_n: int = 20) -> str`
- Purpose: Identify blockers/waiters and suggest safe remediation order.
- Example call:
```text
oracle_blocking_sessions_analyzer(top_n=20)
```

## 9. `oracle_child_cursor_explosion_detector`
- Signature: `oracle_child_cursor_explosion_detector(min_children: int = 5, top_n: int = 50) -> str`
- Purpose: Detect SQL IDs with many child cursors and non-sharing reasons.
- Example call:
```text
oracle_child_cursor_explosion_detector(min_children=5, top_n=50)
```

## 10. `oracle_compare_awr_reports`
- Signature: `oracle_compare_awr_reports(baseline_report_text: Optional[str] = None, baseline_report_path: Optional[str] = None, target_report_text: Optional[str] = None, target_report_path: Optional[str] = None, begin_snap_id_1: Optional[int] = None, end_snap_id_1: Optional[int] = None, begin_snap_id_2: Optional[int] = None, end_snap_id_2: Optional[int] = None, begin_snap_id_baseline: Optional[int] = None, end_snap_id_baseline: Optional[int] = None, begin_snap_id_target: Optional[int] = None, end_snap_id_target: Optional[int] = None, dbid: Optional[int] = None, instance_number: Optional[int] = None) -> str`
- Purpose: Compare two AWR reports and highlight metric deltas and potential regressions.
- Example call:
```text
oracle_compare_awr_reports(baseline_report_text=None, baseline_report_path=None, target_report_text=None, target_report_path=None, begin_snap_id_1=None, end_snap_id_1=None, begin_snap_id_2=None, end_snap_id_2=None, begin_snap_id_baseline=None, end_snap_id_baseline=None, begin_snap_id_target=None, end_snap_id_target=None, dbid=None, instance_number=None)
```

## 11. `oracle_cpu_pressure_analyzer`
- Signature: `oracle_cpu_pressure_analyzer(window_minutes: int = 15, top_n: int = 20) -> str`
- Purpose: Analyze CPU pressure from DB and host metrics with top SQL CPU consumers.
- Example call:
```text
oracle_cpu_pressure_analyzer(window_minutes=15, top_n=20)
```

## 12. `oracle_create_awr_snapshot`
- Signature: `oracle_create_awr_snapshot() -> str`
- Purpose: Create AWR snapshot and return latest snapshot ID.
- Example call:
```text
oracle_create_awr_snapshot()
```

## 13. `oracle_create_spm_baseline_from_source`
- Signature: `oracle_create_spm_baseline_from_source(sql_id: str, plan_hash_value: Optional[int] = None, source: str = 'cursor', begin_snap_id: Optional[int] = None, end_snap_id: Optional[int] = None, fixed: bool = True, enabled: bool = True, confirm_apply: bool = False) -> str`
- Purpose: Create SQL Plan Baseline from cursor cache or AWR.
- Example call:
```text
oracle_create_spm_baseline_from_source(sql_id="<value>", plan_hash_value=None, source="cursor", begin_snap_id=None, end_snap_id=None, fixed=True, enabled=True, confirm_apply=False)
```

## 14. `oracle_dbre_help_catalog`
- Signature: `oracle_dbre_help_catalog(topic: Optional[str] = None) -> str`
- Purpose: Built-in runbook that maps common Oracle symptoms to recommended MCP tools.
- Example call:
```text
oracle_dbre_help_catalog(topic=None)
```

## 15. `oracle_execute_readonly_query`
- Signature: `oracle_execute_readonly_query(sql: str, binds: Optional[Dict[str, Any]] = None, max_rows: int = 200) -> str`
- Purpose: Execute a read-only SQL query (SELECT/WITH only) with optional bind values.
- Example call:
```text
oracle_execute_readonly_query(sql="<value>", binds=None, max_rows=200)
```

## 16. `oracle_generate_bind_query_from_vsql`
- Signature: `oracle_generate_bind_query_from_vsql(sql_id: Optional[str] = None, sql_text: Optional[str] = None, include_capture_values: bool = True) -> str`
- Purpose: Generate a bind-variable SQL template by querying V$SQL and V$SQL_BIND_CAPTURE.
- Example call:
```text
oracle_generate_bind_query_from_vsql(sql_id=None, sql_text=None, include_capture_values=True)
```

## 17. `oracle_generate_sql_profile_script`
- Signature: `oracle_generate_sql_profile_script(sql_id: str, plan_hash_value: int, profile_name: Optional[str] = None, force_match: bool = False, category: str = 'DEFAULT') -> str`
- Purpose: Generate a SQL script to create a manual SQL Profile from known plan outline hints,
- Example call:
```text
oracle_generate_sql_profile_script(sql_id="<value>", plan_hash_value=1, profile_name=None, force_match=False, category="DEFAULT")
```

## 18. `oracle_get_awr_report_text`
- Signature: `oracle_get_awr_report_text(begin_snap_id: int, end_snap_id: int, dbid: Optional[int] = None, instance_number: Optional[int] = None) -> str`
- Purpose: Generate AWR report text directly from DBMS_WORKLOAD_REPOSITORY.AWR_REPORT_TEXT.
- Example call:
```text
oracle_get_awr_report_text(begin_snap_id=1, end_snap_id=1, dbid=None, instance_number=None)
```

## 19. `oracle_health_check`
- Signature: `oracle_health_check() -> str`
- Purpose: Validate Oracle DB connectivity and return database identity details.
- Example call:
```text
oracle_health_check()
```

## 20. `oracle_index_advisor_lite`
- Signature: `oracle_index_advisor_lite(owner: Optional[str] = None, top_n: int = 50) -> str`
- Purpose: Provide lightweight index health/advisory signals (duplicate prefixes and poor clustering).
- Example call:
```text
oracle_index_advisor_lite(owner=None, top_n=50)
```

## 21. `oracle_index_effectiveness_and_fk_gaps`
- Signature: `oracle_index_effectiveness_and_fk_gaps(owner: Optional[str] = None, top_n: int = 200) -> str`
- Purpose: Index effectiveness signals and foreign-key missing index gaps.
- Example call:
```text
oracle_index_effectiveness_and_fk_gaps(owner=None, top_n=200)
```

## 22. `oracle_latch_mutex_hotspots`
- Signature: `oracle_latch_mutex_hotspots(window_minutes: int = 60, top_n: int = 20, source: str = 'auto') -> str`
- Purpose: Latch/mutex contention hotspots inspired by latch/mutex profiling patterns.
- Example call:
```text
oracle_latch_mutex_hotspots(window_minutes=60, top_n=20, source="auto")
```

## 23. `oracle_latency_breakdown_report`
- Signature: `oracle_latency_breakdown_report(window_minutes: int = 30, top_n: int = 30) -> str`
- Purpose: Latency and DB-time breakdown by ASH wait class/event and top SQL.
- Example call:
```text
oracle_latency_breakdown_report(window_minutes=30, top_n=30)
```

## 24. `oracle_lock_chain_analyzer`
- Signature: `oracle_lock_chain_analyzer(top_n: int = 50) -> str`
- Purpose: Analyze lock chains/blockers with suggested kill syntax.
- Example call:
```text
oracle_lock_chain_analyzer(top_n=50)
```

## 25. `oracle_memory_pressure_report`
- Signature: `oracle_memory_pressure_report(top_n: int = 25) -> str`
- Purpose: SGA/PGA pressure report with top memory consumers.
- Example call:
```text
oracle_memory_pressure_report(top_n=25)
```

## 26. `oracle_oem_long_running_queries`
- Signature: `oracle_oem_long_running_queries(min_elapsed_seconds: int = 5, threshold_seconds: Optional[int] = None, window_minutes: int = 60, top_n: int = 50, only_active: bool = True) -> str`
- Purpose: OEM-style long-running SQL activity view using GV$SQL_MONITOR.
- Example call:
```text
oracle_oem_long_running_queries(min_elapsed_seconds=5, threshold_seconds=None, window_minutes=60, top_n=50, only_active=True)
```

## 27. `oracle_parameter_change_audit`
- Signature: `oracle_parameter_change_audit(top_n: int = 200) -> str`
- Purpose: Audit current non-default/modified parameters and SPFILE overrides.
- Example call:
```text
oracle_parameter_change_audit(top_n=200)
```

## 28. `oracle_parameter_timeline_diff`
- Signature: `oracle_parameter_timeline_diff(begin_snap_id: int, end_snap_id: int, top_n: int = 500) -> str`
- Purpose: Compare parameter values across two snapshots (timeline diff).
- Example call:
```text
oracle_parameter_timeline_diff(begin_snap_id=1, end_snap_id=1, top_n=500)
```

## 29. `oracle_planx_sql_id`
- Signature: `oracle_planx_sql_id(sql_id: str, lookback_days: int = 14, include_plan_text: bool = True, plan_format: str = 'ALLSTATS LAST +PEEKED_BINDS +OUTLINE') -> str`
- Purpose: PlanX-style SQL_ID diagnostics: SQL text, plan performance (memory/AWR), binds, ASH waits, and optional DBMS_XPLAN output.
- Example call:
```text
oracle_planx_sql_id(sql_id="<value>", lookback_days=14, include_plan_text=True, plan_format="ALLSTATS LAST +PEEKED_BINDS +OUTLINE")
```

## 30. `oracle_purge_cursor_by_sql_id`
- Signature: `oracle_purge_cursor_by_sql_id(sql_id: str, confirm_apply: bool = False) -> str`
- Purpose: Purge cursor(s) for a SQL_ID using DBMS_SHARED_POOL.PURGE.
- Example call:
```text
oracle_purge_cursor_by_sql_id(sql_id="<value>", confirm_apply=False)
```

## 31. `oracle_rac_gc_hotspots`
- Signature: `oracle_rac_gc_hotspots(window_minutes: int = 60, top_n: int = 30, source: str = 'auto') -> str`
- Purpose: RAC Global Cache hotspot analysis from ASH (gc* events by instance/sql/object).
- Example call:
```text
oracle_rac_gc_hotspots(window_minutes=60, top_n=30, source="auto")
```

## 32. `oracle_role_privilege_audit`
- Signature: `oracle_role_privilege_audit(username: Optional[str] = None, include_object_privileges: bool = False, top_n: int = 200) -> str`
- Purpose: Audit user roles and privileges, highlighting risky grants.
- Example call:
```text
oracle_role_privilege_audit(username=None, include_object_privileges=False, top_n=200)
```

## 33. `oracle_schema_drift_checker`
- Signature: `oracle_schema_drift_checker(schema_a: str, schema_b: str) -> str`
- Purpose: Compare object inventory and invalid objects between two schemas in the same database.
- Example call:
```text
oracle_schema_drift_checker(schema_a="<value>", schema_b="<value>")
```

## 34. `oracle_session_delta_sampler`
- Signature: `oracle_session_delta_sampler(sid: Optional[int] = None, serial: Optional[int] = None, sql_id: Optional[str] = None, module: Optional[str] = None, sample_seconds: int = 5, samples: int = 3) -> str`
- Purpose: Lightweight session delta sampler inspired by session delta sampling patterns.
- Example call:
```text
oracle_session_delta_sampler(sid=None, serial=None, sql_id=None, module=None, sample_seconds=5, samples=3)
```

## 35. `oracle_session_leak_detector`
- Signature: `oracle_session_leak_detector(idle_minutes: int = 30, top_n: int = 100) -> str`
- Purpose: Detect likely leaked/inactive sessions by module/program/machine patterns.
- Example call:
```text
oracle_session_leak_detector(idle_minutes=30, top_n=100)
```

## 36. `oracle_session_pressure_dashboard`
- Signature: `oracle_session_pressure_dashboard(top_n: int = 25) -> str`
- Purpose: Session pressure dashboard: sessions, open cursors, PGA consumers, active SQL concentration.
- Example call:
```text
oracle_session_pressure_dashboard(top_n=25)
```

## 37. `oracle_short_window_activity_sample`
- Signature: `oracle_short_window_activity_sample(window_seconds: int = 15, by: str = 'sql_id', top_n: int = 20) -> str`
- Purpose: Short-window activity sample for active sessions and top dimensions.
- Example call:
```text
oracle_short_window_activity_sample(window_seconds=15, by="sql_id", top_n=20)
```

## 38. `oracle_spm_baseline_manager`
- Signature: `oracle_spm_baseline_manager(action: str = 'list', sql_handle: Optional[str] = None, plan_name: Optional[str] = None, top_n: int = 200, confirm_apply: bool = False) -> str`
- Purpose: Manage SQL Plan Baselines: list, enable, disable, fix, unfix, drop, evolve.
- Example call:
```text
oracle_spm_baseline_manager(action="list", sql_handle=None, plan_name=None, top_n=200, confirm_apply=False)
```

## 39. `oracle_spm_baseline_pack_unpack`
- Signature: `oracle_spm_baseline_pack_unpack(action: str = 'list', table_name: str = 'MCP_SPM_STGTAB', table_owner: Optional[str] = None, sql_handle: Optional[str] = None, enabled_only: bool = False, confirm_apply: bool = False) -> str`
- Purpose: Create/list/pack/unpack SPM baseline staging table using DBMS_SPM.
- Example call:
```text
oracle_spm_baseline_pack_unpack(action="list", table_name="MCP_SPM_STGTAB", table_owner=None, sql_handle=None, enabled_only=False, confirm_apply=False)
```

## 40. `oracle_sql_dependency_impact_map`
- Signature: `oracle_sql_dependency_impact_map(sql_id: str, top_n: int = 200) -> str`
- Purpose: Map SQL dependency impact: objects referenced by plan with segment footprint.
- Example call:
```text
oracle_sql_dependency_impact_map(sql_id="<value>", top_n=200)
```

## 41. `oracle_sql_hotlist_manager`
- Signature: `oracle_sql_hotlist_manager(action: str = 'list', sql_id: Optional[str] = None, severity: str = 'medium', tags: Optional[List[str]] = None, note: Optional[str] = None, top_n: int = 20) -> str`
- Purpose: Manage local SQL hotlist for incident focus (list/add/remove/auto).
- Example call:
```text
oracle_sql_hotlist_manager(action="list", sql_id=None, severity="medium", tags=None, note=None, top_n=20)
```

## 42. `oracle_sql_monitor_like_analysis`
- Signature: `oracle_sql_monitor_like_analysis(sql_id: str, window_minutes: int = 60, source: str = 'auto', top_n: int = 30) -> str`
- Purpose: SQL monitor-like plan line time attribution using ASH samples.
- Example call:
```text
oracle_sql_monitor_like_analysis(sql_id="<value>", window_minutes=60, source="auto", top_n=30)
```

## 43. `oracle_sql_patch_quarantine`
- Signature: `oracle_sql_patch_quarantine(action: str = 'list', sql_id: Optional[str] = None, patch_name: Optional[str] = None, hint_text: Optional[str] = None, description: Optional[str] = None, category: str = 'DEFAULT', validate: bool = True, confirm_apply: bool = False) -> str`
- Purpose: List/create/drop SQL patches for emergency SQL behavior control.
- Example call:
```text
oracle_sql_patch_quarantine(action="list", sql_id=None, patch_name=None, hint_text=None, description=None, category="DEFAULT", validate=True, confirm_apply=False)
```

## 44. `oracle_sql_plan_regression_detector`
- Signature: `oracle_sql_plan_regression_detector(days: int = 7, top_n: int = 20, window_minutes: Optional[int] = None) -> str`
- Purpose: Detect potential SQL plan regressions from AWR SQL stats by comparing best vs worst plans.
- Example call:
```text
oracle_sql_plan_regression_detector(days=7, top_n=20, window_minutes=None)
```

## 45. `oracle_sql_plan_rescue_playbook`
- Signature: `oracle_sql_plan_rescue_playbook(sql_id: str, lookback_days: int = 14, preferred_plan_hash_value: Optional[int] = None, top_plans: int = 10) -> str`
- Purpose: Build a recovery playbook for SQL plan regressions: find best historical plan,
- Example call:
```text
oracle_sql_plan_rescue_playbook(sql_id="<value>", lookback_days=14, preferred_plan_hash_value=None, top_plans=10)
```

## 46. `oracle_sql_rewrite_benchmark_assistant`
- Signature: `oracle_sql_rewrite_benchmark_assistant(sql_id: Optional[str] = None, original_sql: Optional[str] = None, rewritten_sql: Optional[str] = None, use_captured_binds: bool = True, bind_sets: Optional[List[Dict[str, Any]]] = None, iterations: int = 3, fetch_rows: int = 200) -> str`
- Purpose: One-stop SQL tuning workflow:
- Example call:
```text
oracle_sql_rewrite_benchmark_assistant(sql_id=None, original_sql=None, rewritten_sql=None, use_captured_binds=True, bind_sets=None, iterations=3, fetch_rows=200)
```

## 47. `oracle_stats_drift_and_staleness_report`
- Signature: `oracle_stats_drift_and_staleness_report(owner: Optional[str] = None, top_n: int = 200) -> str`
- Purpose: Report stale/missing/locked stats and table modification drift.
- Example call:
```text
oracle_stats_drift_and_staleness_report(owner=None, top_n=200)
```

## 48. `oracle_stats_health_check`
- Signature: `oracle_stats_health_check(owner: Optional[str] = None, top_n: int = 100) -> str`
- Purpose: Inspect table statistics health: stale stats, missing stats, locked stats.
- Example call:
```text
oracle_stats_health_check(owner=None, top_n=100)
```

## 49. `oracle_suggest_query_rewrite`
- Signature: `oracle_suggest_query_rewrite(sql_text: str, sql_id: Optional[str] = None) -> str`
- Purpose: Suggest SQL rewrite opportunities for a custom query.
- Example call:
```text
oracle_suggest_query_rewrite(sql_text="<value>", sql_id=None)
```

## 50. `oracle_tablespace_capacity_forecast`
- Signature: `oracle_tablespace_capacity_forecast(days: int = 30) -> str`
- Purpose: Report current tablespace utilization and simple growth projection.
- Example call:
```text
oracle_tablespace_capacity_forecast(days=30)
```

## 51. `oracle_test_query_with_binds`
- Signature: `oracle_test_query_with_binds(original_sql: str, candidate_sql: Optional[str] = None, bind_sets: Optional[List[Dict[str, Any]]] = None, iterations: int = 3, fetch_rows: int = 200) -> str`
- Purpose: Benchmark original vs candidate read-only query using provided bind sets.
- Example call:
```text
oracle_test_query_with_binds(original_sql="<value>", candidate_sql=None, bind_sets=None, iterations=3, fetch_rows=200)
```

## 52. `oracle_top_segments_by_stat`
- Signature: `oracle_top_segments_by_stat(window_minutes: int = 60, top_n: int = 20, source: str = 'auto', metric: str = 'samples') -> str`
- Purpose: Top segments by ASH-derived activity metric (top segment stat patterns).
- Example call:
```text
oracle_top_segments_by_stat(window_minutes=60, top_n=20, source="auto", metric="samples")
```

## 53. `oracle_wait_chain_analyzer`
- Signature: `oracle_wait_chain_analyzer(window_minutes: int = 30, top_n: int = 30, source: str = 'auto') -> str`
- Purpose: Historical blocker->waiter chain analysis using ASH.
- Example call:
```text
oracle_wait_chain_analyzer(window_minutes=30, top_n=30, source="auto")
```

## 54. `oracle_waits_hotspots`
- Signature: `oracle_waits_hotspots(hours: int = 1, top_n: int = 15) -> str`
- Purpose: Analyze wait hotspots from ASH and return top wait classes/events, SQL IDs, and modules.
- Example call:
```text
oracle_waits_hotspots(hours=1, top_n=15)
```
