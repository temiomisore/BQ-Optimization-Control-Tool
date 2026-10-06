-- =============================================================================
-- 03_derived_views.sql — first derived layer over the raw telemetry.
-- These are the views the rules engine (and the verifier baselines) read.
-- Run after 01/02. All pricing comes from optimizer_ops.collector_config so
-- price changes never rewrite raw history.
-- =============================================================================

-- Convenience: config as one row of columns.
CREATE OR REPLACE VIEW optimizer_ops.v_config AS
SELECT
  MAX(IF(key = 'on_demand_usd_per_tib',              value, NULL)) AS on_demand_usd_per_tib,
  MAX(IF(key = 'storage_logical_active_usd_per_gib', value, NULL)) AS p_log_active,
  MAX(IF(key = 'storage_logical_lt_usd_per_gib',     value, NULL)) AS p_log_lt,
  MAX(IF(key = 'storage_physical_active_usd_per_gib',value, NULL)) AS p_phy_active,
  MAX(IF(key = 'storage_physical_lt_usd_per_gib',    value, NULL)) AS p_phy_lt,
  MAX(IF(key = 'slot_hour_usd_standard',             value, NULL)) AS slot_hour_usd_standard,
  MAX(IF(key = 'slot_hour_usd_enterprise',           value, NULL)) AS slot_hour_usd_enterprise,
  MAX(IF(key = 'slot_hour_usd_enterprise_plus',      value, NULL)) AS slot_hour_usd_enterprise_plus,
  MAX(IF(key = 'slot_hour_usd_enterprise_1yr',       value, NULL)) AS slot_hour_usd_enterprise_1yr,
  MAX(IF(key = 'partition_candidate_min_bytes',      value, NULL)) AS partition_min_bytes,
  MAX(IF(key = 'cluster_candidate_min_bytes',        value, NULL)) AS cluster_min_bytes
FROM optimizer_ops.collector_config;

-- -----------------------------------------------------------------------------
-- Reservation slot rates (edition -> $/slot-hour), from the latest reservation
-- snapshot. Populated only when reservation_admin_project is configured; jobs on
-- reservations we can't see are priced at the ENTERPRISE rate and labelled so.
-- JOBS.reservation_id looks like 'admin-project:US.reservation-name'.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW optimizer_ops.v_reservation_rates AS
WITH latest AS (
  SELECT region, project_id, raw_json
  FROM optimizer_ops.object_state_raw_daily
  WHERE kind = 'RESERVATION'
    AND snapshot_date = (SELECT MAX(snapshot_date) FROM optimizer_ops.object_state_raw_daily
                         WHERE kind = 'RESERVATION')
),
parsed AS (
  SELECT
    region,
    LOWER(COALESCE(JSON_VALUE(raw_json, '$.project_id'), project_id))  AS admin_project,
    LOWER(JSON_VALUE(raw_json, '$.reservation_name'))                  AS reservation_name,
    UPPER(COALESCE(JSON_VALUE(raw_json, '$.edition'), 'ENTERPRISE'))   AS edition,
    SAFE_CAST(JSON_VALUE(raw_json, '$.slot_capacity') AS INT64)        AS baseline_slots,
    SAFE_CAST(JSON_VALUE(raw_json, '$.autoscale.max_slots') AS INT64)  AS autoscale_max_slots
  FROM latest
)
SELECT
  p.admin_project, p.reservation_name, p.edition, p.baseline_slots, p.autoscale_max_slots, p.region,
  CASE p.edition
    WHEN 'STANDARD'        THEN c.slot_hour_usd_standard
    WHEN 'ENTERPRISE_PLUS' THEN c.slot_hour_usd_enterprise_plus
    ELSE c.slot_hour_usd_enterprise
  END AS slot_hour_usd
FROM parsed p
CROSS JOIN optimizer_ops.v_config c
WHERE p.reservation_name IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY p.admin_project, p.reservation_name ORDER BY p.region) = 1;

-- -----------------------------------------------------------------------------
-- Costed jobs. NOTE the billing-mode honesty rule: est_on_demand_usd is only a
-- real dollar figure for jobs that ran on-demand (reservation_id IS NULL).
-- For reservation jobs, slot_ms is the currency — price it against the
-- edition rate in the rules engine, never against bytes.
-- est_cost_usd applies that rule per job: bytes x $/TiB for on-demand jobs,
-- slot-hours x edition rate for reservation jobs. Use it for every dollar
-- figure; est_on_demand_usd stays only for backward compatibility.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW optimizer_ops.v_jobs_costed AS
SELECT
  j.*,
  SAFE_DIVIDE(j.total_bytes_billed, POW(1024, 4)) * c.on_demand_usd_per_tib AS est_on_demand_usd,
  (j.reservation_id IS NULL)                                                AS ran_on_demand,
  IF(j.reservation_id IS NULL, 'ON_DEMAND', 'RESERVATION')                  AS billing_mode,
  IF(j.reservation_id IS NULL, NULL,
     COALESCE(rr.slot_hour_usd, c.slot_hour_usd_enterprise))                AS slot_rate_usd,
  IF(j.reservation_id IS NULL, NULL,
     IF(rr.slot_hour_usd IS NULL, 'ASSUMED_ENTERPRISE_RATE',
        CONCAT('RESERVATION_', rr.edition)))                                AS slot_rate_source,
  IF(j.reservation_id IS NULL, 0,
     SAFE_DIVIDE(j.total_slot_ms, 3600000)
       * COALESCE(rr.slot_hour_usd, c.slot_hour_usd_enterprise))            AS est_reservation_usd,
  IF(j.reservation_id IS NULL,
     SAFE_DIVIDE(j.total_bytes_billed, POW(1024, 4)) * c.on_demand_usd_per_tib,
     SAFE_DIVIDE(j.total_slot_ms, 3600000)
       * COALESCE(rr.slot_hour_usd, c.slot_hour_usd_enterprise))            AS est_cost_usd
FROM optimizer_ops.jobs_events j
CROSS JOIN optimizer_ops.v_config c
LEFT JOIN optimizer_ops.v_reservation_rates rr
  ON  rr.admin_project    = LOWER(REGEXP_EXTRACT(j.reservation_id, r'^(.+):[^:]+$'))
  AND rr.reservation_name = LOWER(REGEXP_EXTRACT(j.reservation_id, r'\.([^.:]+)$'))
WHERE j.job_type = 'QUERY'
  AND j.statement_type != 'SCRIPT';   -- parents double-count their children

-- -----------------------------------------------------------------------------
-- Per-table read/write stats, 90 days.
-- !! SCOPE WARNING: this sees only jobs collected by THIS deployment.
-- A table unread here may be read daily from another project. No archive or
-- delete recommendation may cite this view without org-wide confirmation
-- (JOBS_BY_ORGANIZATION collection and/or Data Access audit logs).
-- Cost attribution: a job that reads N tables is split across them (by table
-- size when every size is known, otherwise 1/N), so joins are never counted
-- N times. bytes_billed_reads_attr / slot_ms_reads_attr use the same split.
-- est_on_demand_usd_reads is the old, un-split, $/TiB-for-every-job
-- figure, kept only so older readers don't break.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW optimizer_ops.v_table_read_write_90d AS
WITH latest_sizes AS (
  SELECT project_id, dataset_id, table_id, MAX(total_logical_bytes) AS logical_bytes
  FROM optimizer_ops.table_state_daily
  WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM optimizer_ops.table_state_daily)
  GROUP BY 1, 2, 3
),
job_tables AS (
  SELECT
    j.region, j.project_id AS billing_project, j.job_id, j.user_email, j.creation_time,
    j.total_bytes_billed, j.total_slot_ms, j.est_on_demand_usd, j.billing_mode, j.est_cost_usd,
    rt.project_id, rt.dataset_id, rt.table_id,
    s.logical_bytes
  FROM optimizer_ops.v_jobs_costed j,
       UNNEST(ARRAY(SELECT DISTINCT AS STRUCT r.project_id, r.dataset_id, r.table_id
                    FROM UNNEST(j.referenced_tables) r)) rt
  LEFT JOIN latest_sizes s
    ON s.project_id = rt.project_id AND s.dataset_id = rt.dataset_id AND s.table_id = rt.table_id
  WHERE j.creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
),
shared AS (
  SELECT
    jt.*,
    COUNT(*) OVER w                                                  AS n_tables,
    COUNTIF(jt.logical_bytes IS NULL OR jt.logical_bytes <= 0) OVER w AS n_unsized,
    SUM(jt.logical_bytes) OVER w                                     AS sum_bytes
  FROM job_tables jt
  WINDOW w AS (PARTITION BY jt.region, jt.billing_project, jt.job_id)
),
reads AS (
  SELECT
    project_id, dataset_id, table_id,
    COUNT(*)                          AS scan_jobs,
    COUNT(DISTINCT user_email)        AS distinct_readers,
    COUNT(DISTINCT billing_project)   AS reader_projects,
    SUM(total_bytes_billed)           AS bytes_billed_reads,
    SUM(total_slot_ms)                AS slot_ms_reads,
    SUM(est_on_demand_usd)            AS est_on_demand_usd_reads,   -- LEGACY: un-split, every job at $/TiB
    SUM(est_cost_usd * share)                                       AS est_cost_usd_reads,
    SUM(IF(billing_mode = 'ON_DEMAND',   est_cost_usd * share, 0))  AS est_od_usd_reads,
    SUM(IF(billing_mode = 'RESERVATION', est_cost_usd * share, 0))  AS est_resv_usd_reads,
    SUM(total_bytes_billed * share)                                 AS bytes_billed_reads_attr,
    SUM(total_slot_ms * share)                                      AS slot_ms_reads_attr,
    COUNTIF(n_tables > 1)             AS multi_table_jobs,
    ARRAY_AGG(DISTINCT billing_project IGNORE NULLS ORDER BY billing_project LIMIT 5) AS billing_projects,
    MAX(creation_time)                AS last_read_at
  FROM (
    SELECT *, IF(n_unsized = 0 AND sum_bytes > 0, SAFE_DIVIDE(logical_bytes, sum_bytes), 1.0 / n_tables) AS share
    FROM shared
  )
  GROUP BY 1, 2, 3
),
writes AS (
  SELECT
    j.destination_table.project_id, j.destination_table.dataset_id, j.destination_table.table_id,
    COUNT(*)              AS write_jobs,
    MAX(j.creation_time)  AS last_write_at,
    SUM(j.total_modified_partitions) AS modified_partitions
  FROM optimizer_ops.jobs_events j
  WHERE j.creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
    AND j.destination_table.table_id IS NOT NULL
    AND j.statement_type != 'SCRIPT'
  GROUP BY 1, 2, 3
)
SELECT
  COALESCE(r.project_id, w.project_id)  AS project_id,
  COALESCE(r.dataset_id, w.dataset_id)  AS dataset_id,
  COALESCE(r.table_id,  w.table_id)     AS table_id,
  r.scan_jobs, r.distinct_readers, r.reader_projects,
  r.bytes_billed_reads, r.slot_ms_reads, r.est_on_demand_usd_reads, r.last_read_at,
  w.write_jobs, w.last_write_at, w.modified_partitions,
  r.est_cost_usd_reads, r.est_od_usd_reads, r.est_resv_usd_reads,
  r.multi_table_jobs, r.billing_projects,
  r.bytes_billed_reads_attr, r.slot_ms_reads_attr
FROM reads r
FULL OUTER JOIN writes w
  ON  r.project_id = w.project_id
  AND r.dataset_id = w.dataset_id
  AND r.table_id   = w.table_id;

-- -----------------------------------------------------------------------------
-- Query families, 28 days — keyed on the normalized-literals hash.
-- This is the verifier's baseline unit AND the MV-candidate signal.
-- est_cost_usd / est_od_usd / est_resv_usd are billing-aware (see v_jobs_costed);
-- billing_project is the project that pays for most of the family's runs.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW optimizer_ops.v_query_families_28d AS
SELECT
  query_hash,
  COUNT(*)                                   AS executions,
  COUNT(DISTINCT user_email)                 AS distinct_users,
  SUM(total_bytes_billed)                    AS total_bytes_billed,
  AVG(total_bytes_billed)                    AS avg_bytes_billed,
  SUM(total_slot_ms)                         AS total_slot_ms,
  SUM(est_on_demand_usd)                     AS est_on_demand_usd,
  APPROX_QUANTILES(duration_ms, 100)[OFFSET(50)] AS p50_duration_ms,
  APPROX_QUANTILES(duration_ms, 100)[OFFSET(95)] AS p95_duration_ms,
  COUNTIF(cache_hit)                         AS cache_hits,
  ANY_VALUE(query_preview)                   AS sample_preview,
  ANY_VALUE(referenced_tables)               AS sample_referenced_tables,
  SUM(est_cost_usd)                                       AS est_cost_usd,
  SUM(IF(billing_mode = 'ON_DEMAND',   est_cost_usd, 0))  AS est_od_usd,
  SUM(IF(billing_mode = 'RESERVATION', est_cost_usd, 0))  AS est_resv_usd,
  COUNTIF(billing_mode = 'RESERVATION')                   AS reservation_executions,
  APPROX_TOP_COUNT(project_id, 1)[SAFE_OFFSET(0)].value   AS billing_project,
  APPROX_TOP_COUNT(user_email, 1)[SAFE_OFFSET(0)].value   AS sample_user_email
FROM optimizer_ops.v_jobs_costed
WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 28 DAY)
  AND query_hash IS NOT NULL
GROUP BY query_hash;

-- -----------------------------------------------------------------------------
-- Dataset storage billing-model gap (rule C1-05 input).
-- Physical billing charges time-travel + fail-safe bytes at the active rate;
-- logical billing does not charge them at all — the math below reflects that.
-- Positive monthly_saving_usd = the flip is worth reviewing.
-- Remember the one-way-door: ~24 h to take effect, 14-day lock to switch back.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW optimizer_ops.v_dataset_storage_billing_gap AS
WITH latest AS (
  SELECT * FROM optimizer_ops.table_state_daily
  WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM optimizer_ops.table_state_daily)
),
per_dataset AS (
  SELECT
    region, project_id, dataset_id,
    SUM(active_logical_bytes)      AS active_logical,
    SUM(long_term_logical_bytes)   AS lt_logical,
    SUM(active_physical_bytes)     AS active_physical,
    SUM(long_term_physical_bytes)  AS lt_physical,
    SUM(time_travel_physical_bytes) AS tt_physical,
    SUM(fail_safe_physical_bytes)   AS fs_physical
  FROM latest
  GROUP BY 1, 2, 3
)
SELECT
  d.*,
  SAFE_DIVIDE(d.active_logical + d.lt_logical,
              NULLIF(d.active_physical + d.lt_physical, 0))            AS compression_ratio,
  (d.active_logical / POW(1024, 3)) * c.p_log_active
    + (d.lt_logical / POW(1024, 3)) * c.p_log_lt                        AS monthly_cost_logical_usd,
  ((d.active_physical + d.tt_physical + d.fs_physical) / POW(1024, 3)) * c.p_phy_active
    + (d.lt_physical / POW(1024, 3)) * c.p_phy_lt                       AS monthly_cost_physical_usd,
  ((d.active_logical / POW(1024, 3)) * c.p_log_active
    + (d.lt_logical / POW(1024, 3)) * c.p_log_lt)
  - (((d.active_physical + d.tt_physical + d.fs_physical) / POW(1024, 3)) * c.p_phy_active
    + (d.lt_physical / POW(1024, 3)) * c.p_phy_lt)                      AS monthly_saving_if_physical_usd
FROM per_dataset d
CROSS JOIN optimizer_ops.v_config c;

-- -----------------------------------------------------------------------------
-- Unpartitioned, heavily-scanned tables with a viable time column
-- (rule C3-01 candidate feed — the rules engine still confirms date-predicate
-- usage from query text / native insights before scoring).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW optimizer_ops.v_unpartitioned_scan_targets AS
WITH latest_state AS (
  SELECT * FROM optimizer_ops.table_state_daily
  WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM optimizer_ops.table_state_daily)
    AND table_type = 'BASE TABLE'
    AND table_id NOT LIKE '%__bqopt_%'
),
latest_cols AS (
  SELECT * FROM optimizer_ops.columns_daily
  WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM optimizer_ops.columns_daily)
),
part_flags AS (
  SELECT project_id, dataset_id, table_id,
         LOGICAL_OR(is_partitioning_column = 'YES')                    AS is_partitioned,
         LOGICAL_OR(clustering_ordinal_position IS NOT NULL)           AS is_clustered,
         ARRAY_AGG(IF(data_type IN ('DATE', 'TIMESTAMP', 'DATETIME'),
                      column_name, NULL) IGNORE NULLS)                 AS time_columns
  FROM latest_cols
  GROUP BY 1, 2, 3
)
SELECT
  s.region, s.project_id, s.dataset_id, s.table_id,
  s.total_logical_bytes,
  f.is_clustered,
  f.time_columns,
  rw.scan_jobs, rw.bytes_billed_reads, rw.est_on_demand_usd_reads, rw.last_read_at,
  rw.est_cost_usd_reads, rw.est_od_usd_reads, rw.est_resv_usd_reads, rw.billing_projects,
  rw.multi_table_jobs
FROM latest_state s
JOIN part_flags f
  ON  f.project_id = s.project_id AND f.dataset_id = s.dataset_id AND f.table_id = s.table_id
LEFT JOIN optimizer_ops.v_table_read_write_90d rw
  ON  rw.project_id = s.project_id AND rw.dataset_id = s.dataset_id AND rw.table_id = s.table_id
CROSS JOIN optimizer_ops.v_config c
WHERE f.is_partitioned = FALSE
  AND s.total_logical_bytes >= c.partition_min_bytes
  AND ARRAY_LENGTH(f.time_columns) > 0
  AND COALESCE(rw.scan_jobs, 0) > 0
ORDER BY rw.bytes_billed_reads DESC;

-- -----------------------------------------------------------------------------
-- Attribution sources (who is accountable for a principal's spend)
-- -----------------------------------------------------------------------------
-- Every attribution view reads these two shims, never the tables directly.
-- `optimizer.cli init` re-points them at the customer's own tables when
-- employee_hierarchy_table / service_account_owner_table are configured
-- (column names are mapped in config.yaml; see optimizer/attribution.py).
CREATE OR REPLACE VIEW optimizer_ops.v_employee_hierarchy_src AS
SELECT user_email, user_name, department, manager_email, director_email, director_name, cost_center
FROM optimizer_ops.employee_hierarchy;

CREATE OR REPLACE VIEW optimizer_ops.v_service_account_owners_src AS
SELECT service_account_email, owner_email, owner_team, director_email, director_name, application
FROM optimizer_ops.service_account_owners;

-- One row per principal. Humans come from the hierarchy; service accounts
-- (PowerBI, ETL ...) resolve to their owning human and then to that human's
-- Director (or the Director named on the mapping row itself).
CREATE OR REPLACE VIEW optimizer_ops.v_principal_directory AS
WITH h AS (
  SELECT LOWER(TRIM(user_email)) AS email,
         ANY_VALUE(user_name) AS display_name, ANY_VALUE(department) AS department,
         ANY_VALUE(director_email) AS director_email, ANY_VALUE(director_name) AS director_name
  FROM optimizer_ops.v_employee_hierarchy_src
  WHERE user_email IS NOT NULL
  GROUP BY 1
),
sa AS (
  SELECT LOWER(TRIM(service_account_email)) AS email,
         ANY_VALUE(LOWER(TRIM(owner_email))) AS owner_email,
         ANY_VALUE(owner_team) AS owner_team,
         ANY_VALUE(director_email) AS sa_director_email,
         ANY_VALUE(director_name) AS sa_director_name,
         ANY_VALUE(application) AS application
  FROM optimizer_ops.v_service_account_owners_src
  WHERE service_account_email IS NOT NULL
  GROUP BY 1
)
SELECT h.email, 'HUMAN' AS principal_type, h.email AS accountable_owner_email, h.display_name,
       h.department, h.director_email, h.director_name, CAST(NULL AS STRING) AS application,
       'EMPLOYEE_HIERARCHY' AS attribution_source
FROM h
WHERE h.email NOT IN (SELECT email FROM sa)
UNION ALL
SELECT sa.email, 'SERVICE_ACCOUNT', sa.owner_email, oh.display_name,
       COALESCE(sa.owner_team, oh.department),
       COALESCE(sa.sa_director_email, oh.director_email),
       COALESCE(sa.sa_director_name, oh.director_name),
       sa.application,
       IF(COALESCE(sa.sa_director_name, oh.director_name) IS NULL,
          'SERVICE_ACCOUNT_OWNER_UNMAPPED_DIRECTOR', 'SERVICE_ACCOUNT_OWNER')
FROM sa
LEFT JOIN h oh ON oh.email = sa.owner_email;

-- Service accounts with spend but no owner (or an owner with no Director):
-- the to-do list for completing the mapping table.
CREATE OR REPLACE VIEW optimizer_ops.v_unmapped_service_accounts AS
SELECT
  LOWER(j.user_email)                                              AS service_account_email,
  IF(d.email IS NULL, 'NO_OWNER_MAPPING', 'OWNER_HAS_NO_DIRECTOR') AS mapping_status,
  ANY_VALUE(d.accountable_owner_email)                             AS owner_email,
  COUNT(*)                                                         AS queries_90d,
  ROUND(SUM(j.est_cost_usd), 2)                                    AS est_spend_usd_90d,
  ARRAY_AGG(DISTINCT j.project_id IGNORE NULLS ORDER BY j.project_id LIMIT 5) AS projects,
  MAX(j.creation_time)                                             AS last_seen_at
FROM optimizer_ops.v_jobs_costed j
LEFT JOIN optimizer_ops.v_principal_directory d
  ON LOWER(j.user_email) = d.email
WHERE ENDS_WITH(LOWER(j.user_email), '.gserviceaccount.com')
  AND (d.email IS NULL OR d.attribution_source = 'SERVICE_ACCOUNT_OWNER_UNMAPPED_DIRECTOR')
  AND j.creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
GROUP BY 1, 2;

-- -----------------------------------------------------------------------------
-- Director & Leadership Spend Rollup
-- -----------------------------------------------------------------------------
-- Aggregates org-wide query telemetry up to Directors and Departments.
-- Service-account spend rolls up through v_principal_directory to the owning
-- human's Director. estimated_spend_usd is billing-aware (slot jobs priced by
-- slot-hours); estimated_on_demand_spend_usd is the legacy every-job-at-$/TiB figure.
CREATE OR REPLACE VIEW optimizer_ops.v_spend_by_director AS
SELECT
  COALESCE(d.director_name, 'Unassigned Director') AS director_name,
  COALESCE(d.director_email, 'unassigned@company.com') AS director_email,
  COALESCE(d.department, 'Cross-Functional / Core DW') AS department,
  j.project_id,
  COUNT(DISTINCT j.user_email) AS active_users,
  COUNT(j.job_id) AS total_queries,
  ROUND(SUM(j.total_bytes_billed) / POW(1024, 4), 2) AS total_billed_tb,
  ROUND(SUM(j.est_on_demand_usd), 2) AS estimated_on_demand_spend_usd,
  ROUND(SUM(j.total_slot_ms) / (1000 * 3600), 1) AS total_slot_hours,
  ROUND(SUM(j.est_cost_usd), 2) AS estimated_spend_usd,
  ROUND(SUM(IF(j.billing_mode = 'ON_DEMAND',   j.est_cost_usd, 0)), 2) AS on_demand_spend_usd,
  ROUND(SUM(IF(j.billing_mode = 'RESERVATION', j.est_cost_usd, 0)), 2) AS reservation_spend_usd,
  COUNTIF(d.principal_type = 'SERVICE_ACCOUNT') AS service_account_queries,
  STRING_AGG(DISTINCT COALESCE(d.attribution_source,
               IF(ENDS_WITH(LOWER(j.user_email), '.gserviceaccount.com'),
                  'UNMAPPED_SERVICE_ACCOUNT', 'UNMAPPED_HUMAN')), '+') AS attribution_sources
FROM optimizer_ops.v_jobs_costed j
LEFT JOIN optimizer_ops.v_principal_directory d
  ON LOWER(j.user_email) = d.email
GROUP BY 1, 2, 3, 4;

-- -----------------------------------------------------------------------------
-- Director-Attributed Recommendations
-- -----------------------------------------------------------------------------
-- Correlates pending recommendations with the Directors whose teams run
-- queries against those target tables, enabling direct executive accountability.
-- Class 4 cards target a query family (target_dataset = 'queries',
-- target_table = query_hash), so they are matched on the query hash instead.
CREATE OR REPLACE VIEW optimizer_ops.v_director_recommendations AS
WITH job_dirs AS (
  SELECT j.job_id, j.query_hash, j.user_email, j.referenced_tables, j.total_bytes_billed,
         d.director_name, d.department, d.attribution_source
  FROM optimizer_ops.v_jobs_costed j
  LEFT JOIN optimizer_ops.v_principal_directory d
    ON LOWER(j.user_email) = d.email
),
table_directors AS (
  SELECT
    rt.project_id AS target_project,
    rt.dataset_id AS target_dataset,
    rt.table_id   AS target_table,
    COALESCE(jd.director_name, 'Central Data Platform') AS director_name,
    COALESCE(jd.department, 'Platform Infrastructure') AS department,
    STRING_AGG(DISTINCT jd.attribution_source, '+' ORDER BY jd.attribution_source) AS attribution_source,
    COUNT(DISTINCT jd.user_email) AS team_readers_count,
    COUNT(jd.job_id) AS team_queries_count,
    ROUND(SUM(jd.total_bytes_billed) / POW(1024, 3), 2) AS team_billed_gb
  FROM job_dirs jd
  CROSS JOIN UNNEST(jd.referenced_tables) rt
  GROUP BY 1, 2, 3, 4, 5
  QUALIFY ROW_NUMBER() OVER(
    PARTITION BY rt.project_id, rt.dataset_id, rt.table_id
    ORDER BY COUNTIF(jd.director_name IS NOT NULL) DESC, COUNT(jd.job_id) DESC
  ) = 1
),
query_directors AS (
  SELECT
    jd.query_hash,
    COALESCE(jd.director_name, 'Central Data Platform') AS director_name,
    COALESCE(jd.department, 'Platform Infrastructure') AS department,
    STRING_AGG(DISTINCT jd.attribution_source, '+' ORDER BY jd.attribution_source) AS attribution_source,
    COUNT(DISTINCT jd.user_email) AS team_readers_count,
    COUNT(jd.job_id) AS team_queries_count,
    ROUND(SUM(jd.total_bytes_billed) / POW(1024, 3), 2) AS team_billed_gb
  FROM job_dirs jd
  WHERE jd.query_hash IS NOT NULL
  GROUP BY 1, 2, 3
  QUALIFY ROW_NUMBER() OVER(
    PARTITION BY jd.query_hash
    ORDER BY COUNTIF(jd.director_name IS NOT NULL) DESC, COUNT(jd.job_id) DESC
  ) = 1
)
SELECT
  r.change_set_id,
  r.target_project,
  r.target_dataset,
  r.target_table,
  r.apply_class,
  r.rule_ids,
  r.gross_monthly_savings_usd,
  r.state,
  COALESCE(td.director_name, qd.director_name, 'Central Data Platform') AS director_name,
  COALESCE(td.department, qd.department, 'Platform Infrastructure') AS department,
  COALESCE(td.team_readers_count, qd.team_readers_count, 0) AS team_readers_count,
  COALESCE(td.team_queries_count, qd.team_queries_count, 0) AS team_queries_count,
  COALESCE(td.team_billed_gb, qd.team_billed_gb, 0.0) AS team_billed_gb,
  r.proposed_change_json,
  COALESCE(td.attribution_source, qd.attribution_source, 'UNATTRIBUTED') AS attribution_source
FROM optimizer_ops.v_pending_review r
LEFT JOIN table_directors td
  ON  COALESCE(td.target_project, r.target_project) = r.target_project
  AND td.target_dataset = r.target_dataset
  AND td.target_table = r.target_table
LEFT JOIN query_directors qd
  ON  r.target_dataset = 'queries'
  AND qd.query_hash = r.target_table;
