-- =============================================================================
-- 04_change_sets.sql — recommendation store (design doc §7) + learning tables.
-- Run after 01_ops_schema.sql. JSON payloads are stored as STRING (json.dumps)
-- for client simplicity; every *_json column holds a JSON document.
-- =============================================================================

CREATE TABLE IF NOT EXISTS optimizer_ops.change_sets (
  change_set_id        STRING NOT NULL,
  created_at           TIMESTAMP NOT NULL,
  rule_ids             ARRAY<STRING>,
  apply_class          INT64,               -- 1..4
  source               STRING,              -- NATIVE_RECOMMENDER | CUSTOM_RULE | ANTIPATTERN_TOOL | MIXED
  native_rec_names     ARRAY<STRING>,       -- full Recommender API names, for write-back

  target_project       STRING,
  target_dataset       STRING,
  target_table         STRING,              -- NULL for dataset/project scope
  target_region        STRING,

  finding_summary      STRING,              -- plain-English what & why
  evidence_json        STRING,
  observation_days     INT64,
  current_config_ddl   STRING,

  proposed_change_json STRING,              -- structured params + generated DDL / PR payload
  execution_route      STRING,              -- CI_PULL_REQUEST | DIRECT_GUARDED
  owner_principal      STRING,
  owner_source         STRING,              -- LABEL | IAC_CODEOWNERS | TOP_WRITER | DATASET_ADMIN | UNKNOWN

  gross_monthly_savings_usd  NUMERIC,
  recurring_monthly_cost_usd NUMERIC,
  one_time_apply_cost_usd    NUMERIC,
  net_monthly_value_usd      NUMERIC,
  savings_basis        STRING,              -- BYTES_ON_DEMAND | SLOT_EDITIONS | STORAGE | MIXED
  confidence           NUMERIC,
  confidence_factors_json STRING,
  score                NUMERIC,

  blast_radius_json    STRING,
  risk_notes           ARRAY<STRING>,

  state                STRING NOT NULL,     -- lifecycle below
  state_history        ARRAY<STRUCT<state STRING, `at` TIMESTAMP, actor STRING, note STRING>>,
  approvals            ARRAY<STRUCT<principal STRING, `at` TIMESTAMP, role STRING>>,
  rejection_reason     STRING,              -- BREAKS_PIPELINE | TABLE_DEPRECATED | SAVINGS_NOT_CREDIBLE | WRONG_OWNER | TIMING | OTHER
  rejection_note       STRING,
  snooze_until         TIMESTAMP,
  expires_at           TIMESTAMP,

  rollback_plan_json     STRING,
  verification_plan_json STRING,            -- frozen baseline lives here
  applied_at             TIMESTAMP,
  verification_result_json STRING,
  realized_over_predicted  NUMERIC,
  progress_json          STRING             -- executor step-by-step progress (Class 3)
)
PARTITION BY DATE(created_at)
CLUSTER BY state, target_dataset;

-- Lifecycle:
--   DETECTED -> SCORED -> PENDING_REVIEW -> {APPROVED | REJECTED | EXPIRED | SUPERSEDED}
--   APPROVED -> SCHEDULED -> APPLYING -> APPLIED -> VERIFYING -> {VERIFIED | REGRESSED}
--   REGRESSED -> ROLLING_BACK -> ROLLED_BACK ;  APPLYING -> FAILED (clean abort)

-- Rolling realized/predicted per rule — feeds the d_history confidence factor.
CREATE TABLE IF NOT EXISTS optimizer_ops.rule_accuracy (
  rule_id                 STRING NOT NULL,
  updated_at              TIMESTAMP,
  applies                 INT64,
  realized_over_predicted NUMERIC            -- rolling mean, clamped 0.25–1.25 at read time
);

-- Watchdogs for Class 2 recurring-cost objects (MV refresh, index storage).
CREATE TABLE IF NOT EXISTS optimizer_ops.cost_watchdogs (
  change_set_id     STRING,
  kind              STRING,     -- MV_REFRESH | INDEX_STORAGE
  target            STRING,     -- fully qualified object
  monthly_limit_usd NUMERIC,
  created_at        TIMESTAMP,
  disabled_at       TIMESTAMP
);

-- Queue views the review app reads.
CREATE OR REPLACE VIEW optimizer_ops.v_pending_review AS
SELECT *
FROM optimizer_ops.change_sets
WHERE state = 'PENDING_REVIEW'
  AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP())
  AND (snooze_until IS NULL OR snooze_until < CURRENT_TIMESTAMP())
ORDER BY net_monthly_value_usd DESC;

CREATE OR REPLACE VIEW optimizer_ops.v_approved_ready AS
SELECT *
FROM optimizer_ops.change_sets
WHERE state IN ('APPROVED', 'SCHEDULED')
ORDER BY score DESC;

CREATE OR REPLACE VIEW optimizer_ops.v_receipts AS
SELECT change_set_id, rule_ids, target_dataset, target_table,
       gross_monthly_savings_usd AS predicted_usd,
       JSON_VALUE(verification_result_json, '$.realized_monthly_usd') AS realized_usd,
       realized_over_predicted, state, applied_at
FROM optimizer_ops.change_sets
WHERE state IN ('VERIFYING', 'VERIFIED', 'REGRESSED', 'ROLLING_BACK', 'ROLLED_BACK');
