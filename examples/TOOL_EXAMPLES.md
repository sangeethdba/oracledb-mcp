# Oracle MCP Tool Examples (LLM-Formatted Responses)

Generated at: 2026-03-14T02:29:44.338100+00:00
DSN: 127.0.0.1:1521/xepdb1

Each section shows a prompt and the response in readable assistant-style bullets.

## oracle_health_check

Prompt:
- Check Oracle connectivity and identity.

Arguments:
- none

Assistant-style response summary:
- status: ok
- db_name: XEPDB1
- instance_name: XE
- service_name: xepdb1
- session_user: SYSTEM

Status: ok

## oracle_execute_readonly_query

Prompt:
- Run a read-only test query.

Arguments:
- sql: select sysdate as now_dt from dual

Assistant-style response summary:
- count: 1
- truncated: False
- max_rows: 200
- rows:
  - item 1:
    - now_dt: 2026-03-14T02:29:46

Status: ok

## oracle_suggest_query_rewrite

Prompt:
- Suggest rewrites for a custom SQL.

Arguments:
- sql_text: select * from mcp_load_orders where trunc(created_at)=trunc(sysdate)

Assistant-style response summary:
- input_sql: select * from mcp_load_orders where trunc(created_at)=trunc(sysdate)
- runtime_context:
- rewrite_suggestions:
  - item 1:
    - issue: Uses SELECT *
    - recommendation: Project only required columns to reduce I/O, CPU, and network transfer.
  - item 2:
    - issue: Function applied to filtered column
    - recommendation: Avoid wrapping indexed columns in functions; transform constants or use function-based indexes.
- next_steps:
  - Capture DBMS_XPLAN.DISPLAY_CURSOR(sql_id => ..., format => 'ALLSTATS LAST').
  - Check cardinality estimates and join order against actual row counts.
  - Validate index selectivity and stale stats before enforcing hints.

Status: ok

## oracle_generate_bind_query_from_vsql

Prompt:
- Generate bind-variable template from V$SQL and bind capture.

Arguments:
- sql_text: select sysdate as now_dt from dual

Assistant-style response summary:
- sql_id: 0fnvpg02c8ywz
- bind_count: 0
- binds:
- template_sql: select sysdate as now_dt from dual
- example_bind_map:
- recommendations:
  - Use bind variables for literals to reduce hard parsing and shared pool churn.
  - Check V$SQL_SHARED_CURSOR if many child cursors exist for the same SQL_ID.
  - For skewed predicates, validate bind peeking/adaptive cursor sharing behavior.

Status: ok

## oracle_analyze_awr_report

Prompt:
- Analyze an AWR text snippet.

Arguments:
- report_text: Elapsed: 60 (mins)
DB Time: 18000
DB CPU: 9000
Average Active Sessions: 5
db file sequential read  12000  5200
log file sync  8000  1200
SQL ID: 8f6t6uk2y6fht


Assistant-style response summary:
- summary_metrics:
  - db_time_s: 18000.0
  - db_cpu_s: 9000.0
  - elapsed_min: 60.0
  - aas: 5.0
  - top_wait_events:
    - item 1:
      - event: db file sequential read
      - waits: 12000.0
      - time_s: 5200.0
    - item 2:
      - event: log file sync
      - waits: 8000.0
      - time_s: 1200.0
  - top_sql_ids:
    - 8f6t6uk2y6fht
- findings:
  - Top observed wait event: db file sequential read
- recommended_actions:
  - Inspect top SQL by DB Time and parse plan changes between snapshots.
  - Correlate top waits with storage, I/O, and application concurrency behavior.
  - Validate stats freshness and segment/index growth during the interval.

Status: ok

## oracle_compare_awr_reports

Prompt:
- Compare two AWR snippets.

Arguments:
- baseline_report_text: Elapsed: 60 (mins)
DB Time: 18000
DB CPU: 9000
Average Active Sessions: 5
db file sequential read  12000  5200

- target_report_text: Elapsed: 60 (mins)
DB Time: 24000
DB CPU: 10000
Average Active Sessions: 7
db file sequential read  20000  7800


Assistant-style response summary:
- baseline:
  - db_time_s: 18000.0
  - db_cpu_s: 9000.0
  - elapsed_min: 60.0
  - aas: 5.0
  - top_wait_events:
    - item 1:
      - event: db file sequential read
      - waits: 12000.0
      - time_s: 5200.0
  - top_sql_ids:
- target:
  - db_time_s: 24000.0
  - db_cpu_s: 10000.0
  - elapsed_min: 60.0
  - aas: 7.0
  - top_wait_events:
    - item 1:
      - event: db file sequential read
      - waits: 20000.0
      - time_s: 7800.0
  - top_sql_ids:
- comparisons:
  - db_time_s:
    - before: 18000.0
    - after: 24000.0
    - delta: 6000.0
    - pct_change: 33.33
  - db_cpu_s:
    - before: 9000.0
    - after: 10000.0
    - delta: 1000.0
    - pct_change: 11.11
  - aas:
    - before: 5.0
    - after: 7.0
    - delta: 2.0
    - pct_change: 40.0
  - elapsed_min:
    - before: 60.0
    - after: 60.0
    - delta: 0.0
    - pct_change: 0.0
- assessment:
  - Potential regression: DB Time increased materially versus baseline.
- next_steps:
  - Compare top SQL_ID overlap and plan hash changes.
  - Check whether workload mix or concurrency changed between windows.
  - Validate system-level bottlenecks (CPU saturation, storage latency, network waits).

Status: ok

## oracle_get_awr_report_text

Prompt:
- Get AWR report text by snapshot range.

Arguments:
- begin_snap_id: 1
- end_snap_id: 2

Assistant-style response summary:
- error: Unable to generate AWR report: not enough valid snapshots in current context.
- dbid: 3062126794
- instance_number: 1
- candidate_snap_ids:
  - 1
- note: Try calling with a wider snapshot range or use root service (XE).

Status: ok

## oracle_waits_hotspots

Prompt:
- Find top waits/SQL/modules from ASH.

Arguments:
- hours: 1
- top_n: 10

Assistant-style response summary:
- window_hours: 1
- top_wait_events:
  - item 1:
    - wait_class: None
    - event: None
    - samples: 892
  - item 2:
    - wait_class: Scheduler
    - event: resmgr:cpu quantum
    - samples: 475
  - item 3:
    - wait_class: Other
    - event: Failed Logon Delay
    - samples: 164
  - item 4:
    - wait_class: Concurrency
    - event: cursor: pin S wait on X
    - samples: 37
  - item 5:
    - wait_class: Commit
    - event: log file sync
    - samples: 15
  - item 6:
    - wait_class: User I/O
    - event: db file sequential read
    - samples: 8
  - item 7:
    - wait_class: Concurrency
    - event: library cache load lock
    - samples: 8
  - item 8:
    - wait_class: Application
    - event: enq: TX - row lock contention
    - samples: 6
  - ... 2 more items
- top_sql_ids:
  - item 1:
    - sql_id: 7kub1nvw8wmq3
    - samples: 57
  - item 2:
    - sql_id: 1h50ks4ncswfn
    - samples: 47
  - item 3:
    - sql_id: g0t052az3rx44
    - samples: 45
  - item 4:
    - sql_id: 9bnjgucukr2bf
    - samples: 40
  - item 5:
    - sql_id: df4jnq7u6nt6h
    - samples: 35
  - item 6:
    - sql_id: acmvv4fhdc9zh
    - samples: 32
  - item 7:
    - sql_id: 1b7ctcz27ywpa
    - samples: 25
  - item 8:
    - sql_id: ampw9ddqufjd3
    - samples: 22
  - ... 2 more items
- top_modules:
  - item 1:
    - module: DBMS_SCHEDULER
    - machine: 0e186f6d82c2
    - samples: 634
  - item 2:
    - module: /usr/local/bin/python3
    - machine: C4GHJ431CY
    - samples: 285
  - item 3:
    - module: JDBC Thin Client
    - machine: oracle-test-app-5c8db89dd6-2jqpk
    - samples: 184
  - item 4:
    - module: SYS_AUTO_STS_MODULE
    - machine: 0e186f6d82c2
    - samples: 148
  - item 5:
    - module: MMON_SLAVE
    - machine: 0e186f6d82c2
    - samples: 111
  - item 6:
    - module: MCP_LOAD
    - machine: C4GHJ431CY
    - samples: 108
  - item 7:
    - module: UNKNOWN
    - machine: 0e186f6d82c2
    - samples: 73
  - item 8:
    - module: sqlplus@0e186f6d82c2 (TNS V1-V3)
    - machine: 0e186f6d82c2
    - samples: 49
  - ... 2 more items
- recommendations:
  - Correlate top wait events with top SQL_IDs before tuning.
  - Check module/machine concentration for app-tier hotspots.
  - Validate if waits are stable or spike-only using shorter windows.

Status: ok

## oracle_blocking_sessions_analyzer

Prompt:
- Find blockers/waiters and potential kill candidates.

Arguments:
- top_n: 10

Assistant-style response summary:
- blocked_sessions:
- blocking_sessions:
- potential_kill_candidates:
- caution: Validate business impact before killing sessions. Avoid killing SYS/SYSTEM/background sessions.

Status: ok

## oracle_role_privilege_audit

Prompt:
- Audit roles and privileges.

Arguments:
- top_n: 30

Assistant-style response summary:
- roles:
  - item 1:
    - grantee: PDB_DBA
    - granted_role: CONNECT
    - admin_option: NO
    - default_role: YES
  - item 2:
    - grantee: PDBADMIN
    - granted_role: PDB_DBA
    - admin_option: YES
    - default_role: YES
  - item 3:
    - grantee: SYS
    - granted_role: AUDIT_VIEWER
    - admin_option: YES
    - default_role: YES
  - item 4:
    - grantee: SYS
    - granted_role: CAPTURE_ADMIN
    - admin_option: YES
    - default_role: YES
  - item 5:
    - grantee: SYS
    - granted_role: GATHER_SYSTEM_STATISTICS
    - admin_option: YES
    - default_role: YES
  - item 6:
    - grantee: SYS
    - granted_role: OPTIMIZER_PROCESSING_RATE
    - admin_option: YES
    - default_role: YES
  - item 7:
    - grantee: SYS
    - granted_role: EM_EXPRESS_ALL
    - admin_option: YES
    - default_role: YES
  - item 8:
    - grantee: SYS
    - granted_role: GSMADMIN_ROLE
    - admin_option: YES
    - default_role: YES
  - ... 22 more items
- system_privileges:
  - item 1:
    - grantee: PDB_DBA
    - privilege: CREATE PLUGGABLE DATABASE
    - admin_option: NO
  - item 2:
    - grantee: PDB_DBA
    - privilege: CREATE SESSION
    - admin_option: NO
  - item 3:
    - grantee: MCP_LOAD
    - privilege: CREATE SEQUENCE
    - admin_option: NO
  - item 4:
    - grantee: MCP_LOAD
    - privilege: CREATE TABLE
    - admin_option: NO
  - item 5:
    - grantee: MCP_LOAD
    - privilege: UNLIMITED TABLESPACE
    - admin_option: NO
  - item 6:
    - grantee: MCP_LOAD
    - privilege: CREATE SESSION
    - admin_option: NO
  - item 7:
    - grantee: SYS
    - privilege: ALTER ANY ANALYTIC VIEW
    - admin_option: NO
  - item 8:
    - grantee: SYS
    - privilege: DROP ANY HIERARCHY
    - admin_option: NO
  - ... 22 more items
- risky_grants:
  - item 1:
    - grantee: SYS
    - privilege: ALTER ANY ANALYTIC VIEW
    - admin_option: NO
  - item 2:
    - grantee: SYS
    - privilege: DROP ANY HIERARCHY
    - admin_option: NO
  - item 3:
    - grantee: SYS
    - privilege: INHERIT ANY PRIVILEGES
    - admin_option: NO
  - item 4:
    - grantee: SYS
    - privilege: CREATE ANY MEASURE FOLDER
    - admin_option: NO
  - item 5:
    - grantee: SYS
    - privilege: DROP ANY CUBE
    - admin_option: NO
  - item 6:
    - grantee: SYS
    - privilege: DROP ANY ASSEMBLY
    - admin_option: NO
  - item 7:
    - grantee: SYS
    - privilege: MANAGE ANY FILE GROUP
    - admin_option: NO
  - item 8:
    - grantee: SYS
    - privilege: DROP ANY SQL PROFILE
    - admin_option: NO
  - ... 7 more items
- recommendations:
  - Enforce least privilege and remove ANY privileges where possible.
  - Review ADMIN OPTION grants and PUBLIC grants as a separate pass.

Status: ok

## oracle_schema_drift_checker

Prompt:
- Compare object drift between two schemas.

Arguments:
- schema_a: SYS
- schema_b: SYSTEM

Assistant-style response summary:
- schema_a: SYS
- schema_b: SYSTEM
- object_count_summary:
  - SYS:
    - CLUSTER: 10
    - CONSUMER GROUP: 18
    - CONTEXT: 17
    - DESTINATION: 2
    - DIRECTORY: 11
    - EDITION: 1
    - EVALUATION CONTEXT: 11
    - FUNCTION: 128
    - ... 31 more fields
  - SYSTEM:
    - FUNCTION: 6
    - INDEX: 162
    - INDEX PARTITION: 79
    - LOB: 11
    - SEQUENCE: 7
    - SYNONYM: 8
    - TABLE: 134
    - TABLE PARTITION: 52
    - ... 2 more fields
- count_deltas:
  - item 1:
    - object_type: CLUSTER
    - schema_a_count: 10
    - schema_b_count: 0
    - delta: -10
  - item 2:
    - object_type: CONSUMER GROUP
    - schema_a_count: 18
    - schema_b_count: 0
    - delta: -18
  - item 3:
    - object_type: CONTEXT
    - schema_a_count: 17
    - schema_b_count: 0
    - delta: -17
  - item 4:
    - object_type: DESTINATION
    - schema_a_count: 2
    - schema_b_count: 0
    - delta: -2
  - item 5:
    - object_type: DIRECTORY
    - schema_a_count: 11
    - schema_b_count: 0
    - delta: -11
  - item 6:
    - object_type: EDITION
    - schema_a_count: 1
    - schema_b_count: 0
    - delta: -1
  - item 7:
    - object_type: EVALUATION CONTEXT
    - schema_a_count: 11
    - schema_b_count: 0
    - delta: -11
  - item 8:
    - object_type: FUNCTION
    - schema_a_count: 128
    - schema_b_count: 6
    - delta: -122
  - ... 31 more items
- invalid_objects:

Status: ok

## oracle_sql_plan_regression_detector

Prompt:
- Detect plan regressions from AWR SQL stats.

Arguments:
- days: 7
- top_n: 10

Assistant-style response summary:
- window_days: 7
- potential_regressions:
  - item 1:
    - sql_id: 8fkf44w2uz074
    - plan_count: 2
    - best_us_per_exec: 8818.36
    - worst_us_per_exec: 21392.26
    - ratio: 2.43
  - item 2:
    - sql_id: 9bnjgucukr2bf
    - plan_count: 2
    - best_us_per_exec: 6525798
    - worst_us_per_exec: 13551966
    - ratio: 2.08
  - item 3:
    - sql_id: 1b7ctcz27ywpa
    - plan_count: 2
    - best_us_per_exec: 5689355
    - worst_us_per_exec: 8860383
    - ratio: 1.56
- recommendations:
  - For high-ratio SQL_IDs, compare plans with DBMS_XPLAN from AWR and cursor cache.
  - Consider SQL Plan Baselines or SQL Profiles only after stats/index checks.

Status: ok

## oracle_stats_health_check

Prompt:
- Check stale/missing/locked statistics.

Arguments:
- top_n: 20

Assistant-style response summary:
- rows:
  - item 1:
    - owner: SYS
    - table_name: METASTYLESHEET
    - stale_stats: YES
    - last_analyzed: 2021-08-17T23:29:33
    - num_rows: 205
    - stattype_locked: None
  - item 2:
    - owner: AUDSYS
    - table_name: AUD$UNIFIED
    - stale_stats: YES
    - last_analyzed: 2021-08-17T23:59:45
    - num_rows: 0
    - stattype_locked: None
  - item 3:
    - owner: SYS
    - table_name: ACCESS$
    - stale_stats: YES
    - last_analyzed: 2021-08-17T23:59:59
    - num_rows: 22732
    - stattype_locked: None
  - item 4:
    - owner: SYS
    - table_name: ATSK$_SCHEDULE_CONTROL
    - stale_stats: YES
    - last_analyzed: 2021-08-18T00:00:11
    - num_rows: 2
    - stattype_locked: None
  - item 5:
    - owner: SYS
    - table_name: ATTRIBUTE_TRANSFORMATIONS$
    - stale_stats: YES
    - last_analyzed: 2021-08-18T00:00:11
    - num_rows: 3
    - stattype_locked: None
  - item 6:
    - owner: SYS
    - table_name: ATSK$_TRACK_DBID
    - stale_stats: YES
    - last_analyzed: 2021-08-18T00:00:11
    - num_rows: 1
    - stattype_locked: None
  - item 7:
    - owner: SYS
    - table_name: ARGUMENT$
    - stale_stats: YES
    - last_analyzed: 2021-08-18T00:00:11
    - num_rows: 163
    - stattype_locked: None
  - item 8:
    - owner: SYS
    - table_name: ATSK$_SETTINGS
    - stale_stats: YES
    - last_analyzed: 2021-08-18T00:00:11
    - num_rows: 16
    - stattype_locked: None
  - ... 12 more items
- summary:
  - stale_count: 20
  - missing_count: 0
  - locked_count: 0
- recommendations:
  - Gather stale or missing table stats before forcing hints.
  - Review locked stats for intentional pinning vs stale drift.

Status: ok

## oracle_index_advisor_lite

Prompt:
- Find duplicate-leading and high-clustering-factor index candidates.

Arguments:
- top_n: 20

Assistant-style response summary:
- index_sample:
  - item 1:
    - owner: SYS
    - table_name: COL$
    - index_name: I_COL1
    - blevel: 2
    - leaf_blocks: 727
    - clustering_factor: 8772
    - num_rows: 120891
  - item 2:
    - owner: SYS
    - table_name: COL$
    - index_name: I_COL2
    - blevel: 1
    - leaf_blocks: 336
    - clustering_factor: 1893
    - num_rows: 120891
  - item 3:
    - owner: SYS
    - table_name: COL$
    - index_name: I_COL3
    - blevel: 1
    - leaf_blocks: 277
    - clustering_factor: 1593
    - num_rows: 120891
  - item 4:
    - owner: SYS
    - table_name: OBJ$
    - index_name: I_OBJ5
    - blevel: 1
    - leaf_blocks: 205
    - clustering_factor: 12356
    - num_rows: 22257
  - item 5:
    - owner: SYS
    - table_name: OBJ$
    - index_name: I_OBJ2
    - blevel: 1
    - leaf_blocks: 204
    - clustering_factor: 12361
    - num_rows: 22257
  - item 6:
    - owner: SYS
    - table_name: WRI$_OPTSTAT_HISTHEAD_HISTORY
    - index_name: I_WRI$_OPTSTAT_HH_OBJ_ICOL_ST
    - blevel: 1
    - leaf_blocks: 190
    - clustering_factor: 1425
    - num_rows: 27107
  - item 7:
    - owner: SYS
    - table_name: DEPENDENCY$
    - index_name: I_DEPENDENCY2
    - blevel: 1
    - leaf_blocks: 176
    - clustering_factor: 12616
    - num_rows: 38162
  - item 8:
    - owner: SYS
    - table_name: DEPENDENCY$
    - index_name: I_DEPENDENCY1
    - blevel: 1
    - leaf_blocks: 160
    - clustering_factor: 496
    - num_rows: 38162
  - ... 12 more items
- duplicate_leading_column_candidates:
  - item 1:
    - owner: MDSYS
    - table_name: SDO_NETWORK_METADATA_TABLE
    - column_name: SDO_OWNER
    - index_count: 6
  - item 2:
    - owner: SYS
    - table_name: HCS_SRC$
    - column_name: HCS_OBJ#
    - index_count: 4
  - item 3:
    - owner: SYS
    - table_name: HCS_AV_COL$
    - column_name: AV#
    - index_count: 3
  - item 4:
    - owner: SYS
    - table_name: HCS_LVL_ORD$
    - column_name: DIM#
    - index_count: 3
  - item 5:
    - owner: SYS
    - table_name: LOCKDOWN_PROF$
    - column_name: PROF#
    - index_count: 3
  - item 6:
    - owner: SYS
    - table_name: RULE_SET_RE$
    - column_name: RS_OBJ#
    - index_count: 3
  - item 7:
    - owner: SYS
    - table_name: DBFS_SFS$_FSTO
    - column_name: VOLID
    - index_count: 3
  - item 8:
    - owner: SYSTEM
    - table_name: LOGMNR_COL$
    - column_name: LOGMNR_UID
    - index_count: 3
  - ... 12 more items
- high_clustering_factor_candidates:
- recommendations:
  - Validate duplicate-leading-column indexes for possible consolidation.
  - High clustering factor may indicate random I/O for range scans.

Status: ok

## oracle_tablespace_capacity_forecast

Prompt:
- Report tablespace utilization and growth signals.

Arguments:
- days: 30

Assistant-style response summary:
- forecast_days: 30
- tablespaces:
  - item 1:
    - tablespace_name: SYSAUX
    - allocated_bytes: 356515840
    - free_bytes: 25886720
    - used_bytes: 330629120
    - maxbytes: 34359721984
    - used_pct: 92.74
    - projection:
  - item 2:
    - tablespace_name: SYSTEM
    - allocated_bytes: 285212672
    - free_bytes: 2752512
    - used_bytes: 282460160
    - maxbytes: 34359721984
    - used_pct: 99.03
    - projection:
  - item 3:
    - tablespace_name: UNDOTBS1
    - allocated_bytes: 32505856
    - free_bytes: 2293760
    - used_bytes: 30212096
    - maxbytes: 34359721984
    - used_pct: 92.94
    - projection:
  - item 4:
    - tablespace_name: USERS
    - allocated_bytes: 20971520
    - free_bytes: 10944512
    - used_bytes: 10027008
    - maxbytes: 34359721984
    - used_pct: 47.81
    - projection:
- recommendations:
  - Alert at 80/90/95% with separate thresholds for temp and undo.
  - Review autoextend maxbytes ceilings to avoid silent exhaustion.

Status: ok

## oracle_session_leak_detector

Prompt:
- Detect likely leaked inactive sessions.

Arguments:
- idle_minutes: 1
- top_n: 20

Assistant-style response summary:
- idle_minutes_threshold: 1
- candidates:
- recommendations:
  - Cross-check with app pool min/max settings and abandoned timeout.
  - Watch for high idle counts from same module+machine fingerprint.

Status: ok

## oracle_parameter_change_audit

Prompt:
- Audit modified/non-default parameters.

Arguments:
- top_n: 50

Assistant-style response summary:
- modified_parameters:
  - item 1:
    - name: _dmm_blas_library
    - value: libora_netlib.so
    - isdefault: FALSE
    - ismodified: FALSE
    - issys_modifiable: DEFERRED
    - isinstance_modifiable: TRUE
  - item 2:
    - name: audit_file_dest
    - value: /opt/oracle/admin/XE/adump
    - isdefault: FALSE
    - ismodified: FALSE
    - issys_modifiable: DEFERRED
    - isinstance_modifiable: TRUE
  - item 3:
    - name: audit_sys_operations
    - value: FALSE
    - isdefault: FALSE
    - ismodified: FALSE
    - issys_modifiable: FALSE
    - isinstance_modifiable: FALSE
  - item 4:
    - name: audit_trail
    - value: NONE
    - isdefault: FALSE
    - ismodified: FALSE
    - issys_modifiable: FALSE
    - isinstance_modifiable: FALSE
  - item 5:
    - name: common_user_prefix
    - value: None
    - isdefault: FALSE
    - ismodified: FALSE
    - issys_modifiable: FALSE
    - isinstance_modifiable: FALSE
  - item 6:
    - name: compatible
    - value: 21.0.0
    - isdefault: FALSE
    - ismodified: FALSE
    - issys_modifiable: FALSE
    - isinstance_modifiable: FALSE
  - item 7:
    - name: control_files
    - value: /opt/oracle/oradata/XE/control01.ctl, /opt/oracle/oradata/XE/control02.ctl
    - isdefault: FALSE
    - ismodified: FALSE
    - issys_modifiable: FALSE
    - isinstance_modifiable: FALSE
  - item 8:
    - name: control_management_pack_access
    - value: DIAGNOSTIC+TUNING
    - isdefault: FALSE
    - ismodified: FALSE
    - issys_modifiable: IMMEDIATE
    - isinstance_modifiable: TRUE
  - ... 17 more items
- spfile_overrides:
  - item 1:
    - name: undo_tablespace
    - value: UNDOTBS1
    - display_value: UNDOTBS1
    - isspecified: TRUE
- high_impact_parameters:
- recommendations:
  - Track parameter deltas alongside deployment and incident timelines.
  - Validate hidden coupling with optimizer and parallel settings before changes.

Status: ok

---

## Additional DBRE Tool Examples


## oracle_short_window_activity_sample

Prompt:
- oracle_short_window_activity_sample(window_seconds=20, by="sql_id", top_n=10)

Arguments:
- window_seconds: 20
- by: sql_id
- top_n: 10

Assistant-style response summary:
- window_seconds: 20
- group_by: sql_id
- top_samples:
  - item 1:
    - sample_key: UNKNOWN
    - samples: 761
    - on_cpu_samples: 202
    - wait_samples: 559
  - item 2:
    - sample_key: 7kub1nvw8wmq3
    - samples: 137
    - on_cpu_samples: 133
    - wait_samples: 4
  - item 3:
    - sample_key: b92u4gf9av6ky
    - samples: 102
    - on_cpu_samples: 102
    - wait_samples: 0
  - item 4:
    - sample_key: fn0stp00nzmj6
    - samples: 48
    - on_cpu_samples: 48
    - wait_samples: 0
  - item 5:
    - sample_key: 1h50ks4ncswfn
    - samples: 47
    - on_cpu_samples: 35
    - wait_samples: 12
  - item 6:
    - sample_key: 9bnjgucukr2bf
    - samples: 46
    - on_cpu_samples: 44
    - wait_samples: 2
  - item 7:
    - sample_key: g0t052az3rx44
    - samples: 45
    - on_cpu_samples: 2
    - wait_samples: 43
  - item 8:
    - sample_key: 1b7ctcz27ywpa
    - samples: 38
    - on_cpu_samples: 37
    - wait_samples: 1
  - ... 2 more items
- top_waits:
  - item 1:
    - wait_class: None
    - event: None
    - samples: 1780
  - item 2:
    - wait_class: Other
    - event: Failed Logon Delay
    - samples: 525
  - item 3:
    - wait_class: Scheduler
    - event: resmgr:cpu quantum
    - samples: 512
  - item 4:
    - wait_class: Concurrency
    - event: cursor: pin S wait on X
    - samples: 37
  - item 5:
    - wait_class: Commit
    - event: log file sync
    - samples: 15
  - item 6:
    - wait_class: Other
    - event: enq: WF - contention
    - samples: 9
  - item 7:
    - wait_class: Other
    - event: PGA memory operation
    - samples: 8
  - item 8:
    - wait_class: Concurrency
    - event: library cache load lock
    - samples: 8
  - ... 2 more items
- note: ASH sample-based quick triage similar to short-window ASH triage.

Status: ok

## oracle_cpu_pressure_analyzer

Prompt:
- oracle_cpu_pressure_analyzer(window_minutes=15, top_n=10)

Arguments:
- window_minutes: 15
- top_n: 10

Assistant-style response summary:
- window_minutes: 15
- sysmetrics:
- osstat:
  - item 1:
    - stat_name: NUM_CPUS
    - value: 2
  - item 2:
    - stat_name: IDLE_TIME
    - value: 523025
  - item 3:
    - stat_name: BUSY_TIME
    - value: 1292366
  - item 4:
    - stat_name: LOAD
    - value: 4.33984375
- ash_wait_class_mix:
  - item 1:
    - wait_class: None
    - samples: 1781
  - item 2:
    - wait_class: Other
    - samples: 550
  - item 3:
    - wait_class: Scheduler
    - samples: 512
  - item 4:
    - wait_class: Concurrency
    - samples: 48
  - item 5:
    - wait_class: User I/O
    - samples: 15
  - item 6:
    - wait_class: Commit
    - samples: 15
  - item 7:
    - wait_class: Application
    - samples: 7
  - item 8:
    - wait_class: Configuration
    - samples: 5
  - ... 1 more items
- top_sql_by_cpu:
  - item 1:
    - sql_id: bms1zfb3sxz8v
    - plan_hash_value: 0
    - executions: 14
    - cpu_s_per_exec: 19.974181
    - elapsed_s_per_exec: 32.821852
    - parsing_schema_name: SYSTEM
    - module: /usr/local/bin/python3
  - item 2:
    - sql_id: b39m8n96gxk7c
    - plan_hash_value: 0
    - executions: 12
    - cpu_s_per_exec: 15.434833
    - elapsed_s_per_exec: 21.308139
    - parsing_schema_name: SYS
    - module: DBMS_SCHEDULER
  - item 3:
    - sql_id: ampw9ddqufjd3
    - plan_hash_value: 0
    - executions: 12
    - cpu_s_per_exec: 15.346038
    - elapsed_s_per_exec: 21.173594
    - parsing_schema_name: SYS
    - module: DBMS_SCHEDULER
  - item 4:
    - sql_id: b92u4gf9av6ky
    - plan_hash_value: 0
    - executions: 153
    - cpu_s_per_exec: 0.727663
    - elapsed_s_per_exec: 1.044492
    - parsing_schema_name: SYS
    - module: /usr/local/bin/python3
  - item 5:
    - sql_id: 7kub1nvw8wmq3
    - plan_hash_value: 1693763048
    - executions: 14
    - cpu_s_per_exec: 5.836616
    - elapsed_s_per_exec: 9.752458
    - parsing_schema_name: SYS
    - module: None
  - item 6:
    - sql_id: 7hnc5ucz8grnt
    - plan_hash_value: 1869011320
    - executions: 12
    - cpu_s_per_exec: 4.460688
    - elapsed_s_per_exec: 6.199378
    - parsing_schema_name: SYS
    - module: SYS_AUTO_STS_MODULE
  - item 7:
    - sql_id: c61ajdcqbqn42
    - plan_hash_value: 2532715356
    - executions: 9
    - cpu_s_per_exec: 5.153595
    - elapsed_s_per_exec: 7.141025
    - parsing_schema_name: SYS
    - module: SYS_AUTO_STS_MODULE
  - item 8:
    - sql_id: fn0stp00nzmj6
    - plan_hash_value: 2224464885
    - executions: 9
    - cpu_s_per_exec: 3.08539
    - elapsed_s_per_exec: 4.332727
    - parsing_schema_name: SYS
    - module: SYS_AUTO_STS_MODULE
  - ... 2 more items

Status: ok

## oracle_latency_breakdown_report

Prompt:
- oracle_latency_breakdown_report(window_minutes=30, top_n=15)

Arguments:
- window_minutes: 30
- top_n: 15

Assistant-style response summary:
- window_minutes: 30
- wait_event_breakdown:
  - item 1:
    - wait_class: None
    - event: None
    - samples: 1782
    - pct: 60.69
  - item 2:
    - wait_class: Other
    - event: Failed Logon Delay
    - samples: 526
    - pct: 17.92
  - item 3:
    - wait_class: Scheduler
    - event: resmgr:cpu quantum
    - samples: 512
    - pct: 17.44
  - item 4:
    - wait_class: Concurrency
    - event: cursor: pin S wait on X
    - samples: 37
    - pct: 1.26
  - item 5:
    - wait_class: Commit
    - event: log file sync
    - samples: 15
    - pct: 0.51
  - item 6:
    - wait_class: Other
    - event: enq: WF - contention
    - samples: 9
    - pct: 0.31
  - item 7:
    - wait_class: User I/O
    - event: db file sequential read
    - samples: 8
    - pct: 0.27
  - item 8:
    - wait_class: Other
    - event: PGA memory operation
    - samples: 8
    - pct: 0.27
  - ... 7 more items
- top_sql_contributors:
  - item 1:
    - sql_id: UNKNOWN
    - samples: 763
    - pct: 25.99
  - item 2:
    - sql_id: 7kub1nvw8wmq3
    - samples: 137
    - pct: 4.67
  - item 3:
    - sql_id: b92u4gf9av6ky
    - samples: 102
    - pct: 3.47
  - item 4:
    - sql_id: fn0stp00nzmj6
    - samples: 48
    - pct: 1.63
  - item 5:
    - sql_id: 1h50ks4ncswfn
    - samples: 47
    - pct: 1.6
  - item 6:
    - sql_id: 9bnjgucukr2bf
    - samples: 46
    - pct: 1.57
  - item 7:
    - sql_id: g0t052az3rx44
    - samples: 45
    - pct: 1.53
  - item 8:
    - sql_id: 1b7ctcz27ywpa
    - samples: 38
    - pct: 1.29
  - ... 7 more items

Status: ok

## oracle_memory_pressure_report

Prompt:
- oracle_memory_pressure_report(top_n=15)

Arguments:
- top_n: 15

Assistant-style response summary:
- sga_top:
  - item 1:
    - name: buffer_cache
    - mb: 608
  - item 2:
    - name: free memory
    - mb: 144
  - item 3:
    - name: SQLA
    - mb: 104.75
  - item 4:
    - name: shared_io_pool
    - mb: 80
  - item 5:
    - name: free memory
    - mb: 63.11
  - item 6:
    - name: KGLH0
    - mb: 41.7
  - item 7:
    - name: free memory
    - mb: 15.53
  - item 8:
    - name: KQR X PO
    - mb: 14.31
  - ... 7 more items
- pga_stats:
  - item 1:
    - name: aggregate PGA target parameter
    - mb: 0
  - item 2:
    - name: total PGA inuse
    - mb: 2.92
  - item 3:
    - name: total PGA allocated
    - mb: 3.81
  - item 4:
    - name: over allocation count
    - mb: 0
- top_session_pga:
  - item 1:
    - sid: 287
    - serial#: 4869
    - username: UNKNOWN
    - module: MMON_SLAVE
    - session_pga_mb: 42.61
  - item 2:
    - sid: 44
    - serial#: 6815
    - username: UNKNOWN
    - module: MMON_SLAVE
    - session_pga_mb: 41.61
  - item 3:
    - sid: 297
    - serial#: 4035
    - username: UNKNOWN
    - module: MMON_SLAVE
    - session_pga_mb: 40.49
  - item 4:
    - sid: 23
    - serial#: 33047
    - username: UNKNOWN
    - module: MMON_SLAVE
    - session_pga_mb: 39.36
  - item 5:
    - sid: 38
    - serial#: 24337
    - username: UNKNOWN
    - module: MMON_SLAVE
    - session_pga_mb: 36.94
  - item 6:
    - sid: 314
    - serial#: 3809
    - username: UNKNOWN
    - module: MMON_SLAVE
    - session_pga_mb: 35.56
  - item 7:
    - sid: 277
    - serial#: 35697
    - username: UNKNOWN
    - module: UNKNOWN
    - session_pga_mb: 19.69
  - item 8:
    - sid: 264
    - serial#: 4857
    - username: UNKNOWN
    - module: UNKNOWN
    - session_pga_mb: 9.96
  - ... 7 more items
- sga_free_signals:
  - item 1:
    - pool: java pool
    - name: free memory
    - mb: 144
  - item 2:
    - pool: shared pool
    - name: free memory
    - mb: 62.78
  - item 3:
    - pool: large pool
    - name: free memory
    - mb: 15.53

Status: ok

## oracle_child_cursor_explosion_detector

Prompt:
- oracle_child_cursor_explosion_detector(min_children=2, top_n=20)

Arguments:
- min_children: 2
- top_n: 20

Assistant-style response summary:
- min_children: 2
- top_sql_with_many_children:
  - item 1:
    - sql_id: 8swypbbr0m372
    - child_count: 12
    - plan_count: 1
    - executions: 994
  - item 2:
    - sql_id: 3un99a0zwp4vd
    - child_count: 12
    - plan_count: 1
    - executions: 994
  - item 3:
    - sql_id: 121ffmrc95v7g
    - child_count: 8
    - plan_count: 1
    - executions: 1964
  - item 4:
    - sql_id: 76qcscs1p75v2
    - child_count: 8
    - plan_count: 1
    - executions: 1101
  - item 5:
    - sql_id: guw87u8x36z8r
    - child_count: 8
    - plan_count: 1
    - executions: 1927
  - item 6:
    - sql_id: 7p5dnxj2hsv5x
    - child_count: 7
    - plan_count: 1
    - executions: 7
  - item 7:
    - sql_id: g0t052az3rx44
    - child_count: 7
    - plan_count: 1
    - executions: 2273
  - item 8:
    - sql_id: 5n1fs4m2n2y0r
    - child_count: 7
    - plan_count: 1
    - executions: 2561
  - ... 12 more items
- shared_cursor_reason_flags:
  - item 1:
    - sql_id: 8swypbbr0m372
    - child_number: 2
    - bind_mismatch: 0
    - optimizer_mismatch: 1
    - stats_row_mismatch: 0
    - language_mismatch: 0
  - item 2:
    - sql_id: 8swypbbr0m372
    - child_number: 3
    - bind_mismatch: 0
    - optimizer_mismatch: 1
    - stats_row_mismatch: 0
    - language_mismatch: 0
  - item 3:
    - sql_id: 8swypbbr0m372
    - child_number: 4
    - bind_mismatch: 0
    - optimizer_mismatch: 1
    - stats_row_mismatch: 0
    - language_mismatch: 0
  - item 4:
    - sql_id: 8swypbbr0m372
    - child_number: 5
    - bind_mismatch: 0
    - optimizer_mismatch: 1
    - stats_row_mismatch: 0
    - language_mismatch: 0
  - item 5:
    - sql_id: 8swypbbr0m372
    - child_number: 7
    - bind_mismatch: 0
    - optimizer_mismatch: 1
    - stats_row_mismatch: 0
    - language_mismatch: 0
  - item 6:
    - sql_id: 8swypbbr0m372
    - child_number: 8
    - bind_mismatch: 0
    - optimizer_mismatch: 1
    - stats_row_mismatch: 0
    - language_mismatch: 0
  - item 7:
    - sql_id: 8swypbbr0m372
    - child_number: 10
    - bind_mismatch: 0
    - optimizer_mismatch: 1
    - stats_row_mismatch: 0
    - language_mismatch: 0
  - item 8:
    - sql_id: 8swypbbr0m372
    - child_number: 11
    - bind_mismatch: 0
    - optimizer_mismatch: 1
    - stats_row_mismatch: 0
    - language_mismatch: 0
  - ... 140 more items

Status: ok

## oracle_sql_hotlist_manager

Prompt:
- oracle_sql_hotlist_manager(action="auto", top_n=10)

Arguments:
- action: auto
- top_n: 10

Assistant-style response summary:
- action: auto
- items:
  - item 1:
    - sql_id: 7kub1nvw8wmq3
    - severity: medium
    - tags:
      - auto
      - ash-top
    - note: auto samples=137
    - updated_at: 2026-03-14T04:41:58.115982Z
  - item 2:
    - sql_id: b92u4gf9av6ky
    - severity: medium
    - tags:
      - auto
      - ash-top
    - note: auto samples=102
    - updated_at: 2026-03-14T04:41:58.115982Z
  - item 3:
    - sql_id: fn0stp00nzmj6
    - severity: medium
    - tags:
      - auto
      - ash-top
    - note: auto samples=48
    - updated_at: 2026-03-14T04:41:58.115982Z
  - item 4:
    - sql_id: 1h50ks4ncswfn
    - severity: medium
    - tags:
      - auto
      - ash-top
    - note: auto samples=47
    - updated_at: 2026-03-14T04:41:58.115982Z
  - item 5:
    - sql_id: 9bnjgucukr2bf
    - severity: medium
    - tags:
      - auto
      - ash-top
    - note: auto samples=46
    - updated_at: 2026-03-14T04:41:58.115982Z
  - item 6:
    - sql_id: g0t052az3rx44
    - severity: medium
    - tags:
      - auto
      - ash-top
    - note: auto samples=45
    - updated_at: 2026-03-14T04:41:58.115982Z
  - item 7:
    - sql_id: 1b7ctcz27ywpa
    - severity: medium
    - tags:
      - auto
      - ash-top
    - note: auto samples=38
    - updated_at: 2026-03-14T04:41:58.115982Z
  - item 8:
    - sql_id: df4jnq7u6nt6h
    - severity: medium
    - tags:
      - auto
      - ash-top
    - note: auto samples=36
    - updated_at: 2026-03-14T04:41:58.115982Z
  - ... 2 more items

Status: ok

## oracle_parameter_timeline_diff

Prompt:
- oracle_parameter_timeline_diff(begin_snap_id=13, end_snap_id=14, top_n=200)

Arguments:
- begin_snap_id: 13
- end_snap_id: 14
- top_n: 200

Assistant-style response summary:
- begin_snap_id: 13
- end_snap_id: 14
- changed_parameters:

Status: ok

## oracle_alert_log_analyzer

Prompt:
- oracle_alert_log_analyzer(window_minutes=180, top_n=50)

Arguments:
- window_minutes: 180
- top_n: 50

Assistant-style response summary:
- window_minutes: 180
- top_error_codes:
- entries:

Status: ok

## oracle_spm_baseline_pack_unpack

Prompt:
- oracle_spm_baseline_pack_unpack(action="list", table_name="MCP_SPM_STGTAB")

Arguments:
- action: list
- table_name: MCP_SPM_STGTAB

Assistant-style response summary:
- action: list
- staging_tables:

Status: ok

## oracle_sql_dependency_impact_map

Prompt:
- oracle_sql_dependency_impact_map(sql_id="3mrzy6ugwvvz4", top_n=100)

Arguments:
- sql_id: 3mrzy6ugwvvz4
- top_n: 100

Assistant-style response summary:
- sql_id: 3mrzy6ugwvvz4
- plan_referenced_objects:
  - item 1:
    - object_owner: MCP_DEMO
    - object_name: MCP_DEMO_ORDERS
    - object_type: TABLE
    - segment_mb: 5
- dependency_edges:

Status: ok
