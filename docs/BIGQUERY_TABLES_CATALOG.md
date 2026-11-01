# 📚 BigQuery Optimization Control Plane: Tables & Data Assets Catalog
*Comprehensive Reference for all **18 Persistent Tables** and **16 Views** in `optimizer_ops`*

---

## 🎯 Executive Overview

When you run the initialization command (or deploy via the 1-click Cloud Shell installer):
```bash
python3 -m optimizer.cli init -p <project_id>
```

The system creates the **`optimizer_ops`** dataset in your home location and executes three foundational DDL scripts plus dynamic attribution shims and schema self-healing stamps:
1. **`sql/01_ops_schema.sql`** *(15 tables)*: Event & state telemetry, Active Assist ingest, pricing constants, HR & service-account ownership tables, multi-region route tracking (`regions_seen`), and the UI settings audit log (`settings_changes`).
2. **`sql/04_change_sets.sql`** *(3 tables + 3 queue views)*: Recommendation state machine (`change_sets`), closed-loop accuracy learning (`rule_accuracy`), recurring-cost watchdogs (`cost_watchdogs`), and queue/receipt views.
3. **`sql/03_derived_views.sql` + `optimizer/attribution.py`** *(13 analytical & attribution views)*: Billing-aware query costing (`v_reservation_rates`, `v_jobs_costed`), proportional multi-table spend attribution, storage compression math, and service-account → human owner → Director rollups (`v_principal_directory`, `v_unmapped_service_accounts`, `v_spend_by_director`, `v_director_recommendations`).
4. **Automatic Schema Self-Heal (`optimizer/schema_guard.py`)**: Stamps a `schema_version` hash label on `v_jobs_costed` and automatically re-runs `init` (non-destructively via `CREATE TABLE IF NOT EXISTS` + `CREATE OR REPLACE VIEW`) whenever DDL files evolve.

Below is the complete breakdown of **every table and view**, **what it does**, **why it is critical**, and **which module populates it**.

---

## 📊 Summary Architecture Map

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       OPTIMIZER_OPS DATASET (18 Tables · 16 Views)                              │
├─────────────────────────────────────┬─────────────────────────────────────┬─────────────────────────────────────┤
│ 1. Raw Telemetry & Ops (15 Tables)  │ 2. Analytical & Attribution (13 Vws)│ 3. Governance & Queue (3 Tbls · 3 V)│
│  • jobs_events                      │  • v_config                         │  • change_sets                      │
│  • jobs_hourly_slots                │  • v_reservation_rates              │  • rule_accuracy                    │
│  • table_state_daily                │  • v_jobs_costed (schema_version)   │  • cost_watchdogs                   │
│  • columns_daily                    │  • v_table_read_write_90d           │  • v_pending_review                 │
│  • table_partitions_daily           │  • v_query_families_28d             │  • v_approved_ready                 │
│  • dataset_state_daily              │  • v_dataset_storage_billing_gap    │  • v_receipts                       │
│  • object_state_raw_daily           │  • v_unpartitioned_scan_targets     │                                     │
│  • recommender_recommendations      │  • v_employee_hierarchy_src         │  (+ ephemeral 24h _stg_* courier    │
│  • recommender_insights             │  • v_service_account_owners_src     │     tables during remote loads)     │
│  • collector_audit                  │  • v_principal_directory            │                                     │
│  • collector_config                 │  • v_unmapped_service_accounts      │                                     │
│  • employee_hierarchy               │  • v_spend_by_director              │                                     │
│  • service_account_owners           │  • v_director_recommendations       │                                     │
│  • regions_seen                     │                                     │                                     │
│  • settings_changes                 │                                     │                                     │
└─────────────────────────────────────┴─────────────────────────────────────┴─────────────────────────────────────┘
```

---

## 1. Raw Telemetry, Attribution, Region & Settings Tables (`sql/01_ops_schema.sql`)

### 1. `optimizer_ops.jobs_events`
* **What it is:** The permanent query execution archive (`MERGE`-keyed on `region, project_id, job_id`). Stores query jobs, scanned/billed bytes, slot milliseconds, reservation IDs, referenced tables, `query_hash`, 1 KB `query_preview`, user emails, and cache hits across all discovered regions and projects.
* **Why it's important:** Standard BigQuery `INFORMATION_SCHEMA` drops query history after 180 days. This table preserves long-term telemetry across the project (`JOBS`) or entire GCP Organization (`JOBS_BY_ORGANIZATION`). When running in org mode (where BigQuery omits SQL text from `JOBS_BY_ORGANIZATION`), step `1b_query_previews` automatically hydrates SQL previews per project from `JOBS_BY_PROJECT`.
* **Populated by:** `sql/02_collector_run.sql` (home location) + `optimizer/remote_collector.py` (remote regions + org-mode `hydrate_query_previews`).

### 2. `optimizer_ops.jobs_hourly_slots`
* **What it is:** Hourly rollups of slot consumption and query concurrency by `(region, hour_ts, project_id, reservation_id)` from `JOBS_TIMELINE` / `JOBS_TIMELINE_BY_ORGANIZATION`.
* **Why it's important:** Drives `W-01` Editions reservation sizing (modeling BigQuery's 50-slot, 1-minute minimum autoscaling increments against actual P50/P95 concurrency).
* **Populated by:** `sql/02_collector_run.sql` (Step 2) + `optimizer/remote_collector.py`.

### 3. `optimizer_ops.table_state_daily`
* **What it is:** Daily snapshot of tables across all discovered projects and regions, recording row counts, partition counts, logical bytes, physical bytes (including time-travel and fail-safe bytes), DDL, and partition filter options.
* **Why it's important:** Tracks table storage growth, compression ratios (`C1-05`), time-travel bloat (`C1-04`), and cold/unused tables (`C3-05`). Uses `TABLE_STORAGE_BY_ORGANIZATION` when available with automatic per-project `TABLE_STORAGE` fallback and fault isolation.
* **Populated by:** `sql/02_collector_run.sql` (home project/region) + `optimizer/remote_collector.py` (sibling projects in home region + all projects in remote regions).

### 4. `optimizer_ops.columns_daily`
* **What it is:** Daily snapshot of columns across discovered projects and regions (`column_name`, `data_type`, `is_partitioning_column`, `clustering_ordinal_position`).
* **Why it's important:** Enables the Rules Engine to find candidate `DATE`/`TIMESTAMP`/`DATETIME` columns for partitioning (`C3-01`), clustering candidates (`C1-01`), and column lists for `SELECT *` rewrites (`C4-01`).
* **Populated by:** `sql/02_collector_run.sql` + `optimizer/remote_collector.py`.

### 5. `optimizer_ops.table_partitions_daily`
* **What it is:** Daily snapshot of individual partition IDs, row counts, storage tiers (`ACTIVE` vs. `LONG_TERM`), and logical bytes.
* **Why it's important:** Detects micro-partition sprawl nearing BigQuery's 10,000-partition ceiling (`C3-03`) and partition skew.
* **Populated by:** `sql/02_collector_run.sql` + `optimizer/remote_collector.py`.

### 6. `optimizer_ops.dataset_state_daily`
* **What it is:** Daily snapshot of dataset locations, options (default expirations, max time-travel hours), and `storage_billing_model` (`LOGICAL` vs. `PHYSICAL`).
* **Why it's important:** Powers staging/scratch expiration hygiene (`C1-03`) and dataset storage billing model flips (`C1-05`).
* **Populated by:** `sql/02_collector_run.sql` + `optimizer/remote_collector.py` + `optimizer/collector_driver.py` (cross-project Datasets API backfill).

### 7. `optimizer_ops.object_state_raw_daily`
* **What it is:** Schema-drift-proof JSON capture of `MATERIALIZED_VIEW`, `VIEW`, `TABLE_CONSTRAINT`, `RESERVATION`, `ASSIGNMENT`, `CAPACITY_COMMITMENT`, and Data Transfer Service `SCHEDULED_QUERY` configs.
* **Why it's important:** Feeds actual edition/commitment slot rates into `v_reservation_rates`, detects PK/FK join constraints (`C1-10`) and zombie scheduled queries (`C1-08`), and lets Class 3 rebuilds pause/resume writers cleanly.
* **Populated by:** `sql/02_collector_run.sql` + `optimizer/remote_collector.py` + `optimizer/collector_driver.py` (DTS API).

### 8. `optimizer_ops.recommender_recommendations`
* **What it is:** Ingested recommendations from Google Cloud Active Assist (`PartitionClusterRecommender`, `materializedview.Recommender`), `MERGE`-keyed on `recommendation_id`.
* **Why it's important:** Pulls native recommendations across org/projects (`RECOMMENDATIONS_BY_ORGANIZATION` with `RECOMMENDATIONS` fallback) so their estimates can be audited against real spend and synced back (`CLAIMED` / `SUCCEEDED` / `DISMISSED`).
* **Populated by:** `sql/02_collector_run.sql` + `optimizer/remote_collector.py`.

### 9. `optimizer_ops.recommender_insights`
* **What it is:** Supporting insights and observation windows behind Google Active Assist recommendations.
* **Why it's important:** Supplies observation-period evidence for confidence scoring (`d_window`) and card details.
* **Populated by:** `sql/02_collector_run.sql` + `optimizer/remote_collector.py`.

### 10. `optimizer_ops.collector_audit`
* **What it is:** Structured ingestion health log (`run_id`, `at`, `step`, `region`, `status`, `row_count`, `message`), partitioned by date with 180-day expiration.
* **Why it's important:** Provides step-by-step per-region observability (including non-fatal per-project permission skips).
* **Populated by:** `sql/02_collector_run.sql` + `optimizer/remote_collector.py` + `optimizer/collector_driver.py`.

### 11. `optimizer_ops.collector_config`
* **What it is:** Key-value table storing Google Cloud pricing constants (`on_demand_usd_per_tib`, logical/physical storage rates, Standard/Enterprise/Enterprise Plus/1-yr commitment slot-hour rates, candidate byte thresholds).
* **Why it's important:** Decouples financial rates from raw telemetry bytes so custom enterprise discounts can be updated in one place without rewriting history.
* **Populated by:** `sql/01_ops_schema.sql` (seeded via idempotent `MERGE`).

### 12. `optimizer_ops.employee_hierarchy`
* **What it is:** Built-in lookup table mapping human `user_email` identities to `user_name`, `department`, `manager_email`, `director_email`, `director_name`, and `cost_center`.
* **Why it's important:** Powers Director/VP spend rollups and card ownership routing out of the box when an external HR table (`employee_hierarchy_table`) is not configured.
* **Populated by:** `sql/01_ops_schema.sql` (seeded on `init`; replaceable via `employee_hierarchy_table` in Settings / `config.yaml`).

### 13. `optimizer_ops.service_account_owners`
* **What it is:** Built-in lookup table mapping automated `service_account_email` principals (Looker, PowerBI, Airflow, dbt, Dataform) to their accountable human `owner_email`, `owner_team`, `director_email`, `director_name`, and `application`.
* **Why it's important:** Service accounts cannot approve cards and have no HR manager; mapping them rolls automated pipeline spend up to the owning human's Director in `v_spend_by_director` and routes table ownership cleanly.
* **Populated by:** Operators or replaced via `service_account_owner_table` + `service_account_owner_columns` in Settings / `config.yaml`.

### 14. `optimizer_ops.regions_seen`
* **What it is:** Multi-region route and discovery ledger (`region`, `first_seen_at`, `last_seen_at`, `dataset_count`, `project_count`, `included`, `source`, `is_new_alert`, `run_id`).
* **Why it's important:** With `regions: auto`, Cloud Asset Inventory discovers every BigQuery dataset location across `asset_scope` on each run, records dataset/project counts per region, and flags newly created regions (`is_new_alert = TRUE`) in the Review UI's **RegionsBar** ("New region detected") while providing a resilient fallback route if Asset API calls ever fail.
* **Populated by:** `optimizer/regions.py` (`regions` and `collect` commands).

### 15. `optimizer_ops.settings_changes`
* **What it is:** Append-only audit log of in-app configuration edits saved from `<app-url>/#settings` (`change_id`, `key`, `action` = `SET` | `RESET`, `value_json`, `previous_value_json`, `previous_override_json`, `changed_by`, `changed_at`, `note`), including the UI editors ACL (`_access.settings_editors`).
* **Why it's important:** Eliminates YAML edits and container redeploys for day-to-day settings while preserving a full immutable audit trail with 1-click **Undo**. Read automatically by both the Review Web UI and the nightly Cloud Run Job (`optimizer/cli.py`).
* **Populated by:** `optimizer/settings_store.py` via `POST /api/settings`, `/api/settings/revert`, and `/api/settings/editors`.

> **Note on Ephemeral Courier Tables (`_stg_*`):** During cross-region or cross-project collection, `optimizer/remote_collector.py` uploads fetched metadata into short-lived `optimizer_ops._stg_<step>_<run>` staging tables (configured with a 24-hour table expiration safety net), `MERGE`s them into the master tables above, and drops them immediately.

---

## 2. Recommendation & Governance Tables (`sql/04_change_sets.sql`)

### 16. `optimizer_ops.change_sets`
* **What it is:** The master control plane state machine. Stores every compiled recommendation card, `target_project`/`target_dataset`/`target_table`/`target_region`, proposed DDL or PR diff, billing-aware `savings_math` evidence, confidence score, FinOps/2-person approvals, rollback plan, and verification receipt.
* **Why it's important:** Drives the entire Review UI across all tabs (**Review queue**, **FinOps & billing**, **PR hand-offs**, **Blocked**, **Regressed**, **Rolled back**, **Receipts**).
* **Populated by:** `optimizer/compiler.py` & `optimizer/store.py` (`rules`), `review_app/main.py` (human decisions), `optimizer/executor/` (`execute` / `pr_handoff.py`), and `optimizer/verifier.py` (`verify`).

### 17. `optimizer_ops.rule_accuracy`
* **What it is:** Historical accuracy ledger tracking `applies` and rolling `realized_over_predicted` ratios per `rule_id`.
* **Why it's important:** Feeds the closed-loop `d_history` confidence factor (clamped `0.25–1.25`) once verified post-window telemetry arrives.
* **Populated by:** `optimizer/verifier.py` (`verify`).

### 18. `optimizer_ops.cost_watchdogs`
* **What it is:** Active budget monitor (`monthly_limit_usd`) for Class 2 recurring-cost objects (`MV_REFRESH`, `INDEX_STORAGE`).
* **Why it's important:** Alerts operators if background refresh/storage spend exceeds the approved monthly budget.
* **Populated by:** `optimizer/executor/class2.py` and checked daily by `optimizer/verifier.py`.

---

## 3. Derived Analytical, Attribution & Queue Views (16 Views)

| # | View Name | Source | What It Does & Why It's Critical |
| :--- | :--- | :--- | :--- |
| 1 | **`v_config`** | `03_derived_views.sql` | Pivots `collector_config` key-values into typed columns for SQL joins. |
| 2 | **`v_reservation_rates`** | `03_derived_views.sql` | Parses reservation editions & 1-yr/3-yr commitments from `object_state_raw_daily` to resolve exact per-reservation `$/slot-hour` rates. |
| 3 | **`v_jobs_costed`** | `03_derived_views.sql` | Prices every non-`SCRIPT` query honestly by billing mode (`ON_DEMAND` bytes vs. `RESERVATION` slot-hours); carries `schema_guard.py`'s `schema_version` label. |
| 4 | **`v_table_read_write_90d`** | `03_derived_views.sql` | Aggregates 90-day read spend, slot-hours, and DML writes per table, splitting multi-table jobs proportionally so spend is never double-counted. |
| 5 | **`v_query_families_28d`** | `03_derived_views.sql` | Groups 28-day queries by normalized `query_hash` with billing-mode spend breakdown for MVs, Class 4 rewrites, and verification baselines. |
| 6 | **`v_dataset_storage_billing_gap`** | `03_derived_views.sql` | Compares logical vs. physical (incl. time-travel + fail-safe) monthly storage cost per dataset (`C1-05`). |
| 7 | **`v_unpartitioned_scan_targets`** | `03_derived_views.sql` | Surfaces large unpartitioned tables (`>= 100 GiB`) with candidate temporal columns and active scan spend (`C3-01`). |
| 8 | **`v_employee_hierarchy_src`** | `03_derived_views.sql` + `attribution.py` | Shim view pointing to `optimizer_ops.employee_hierarchy` or your configured external HR table + column map. |
| 9 | **`v_service_account_owners_src`** | `03_derived_views.sql` + `attribution.py` | Shim view pointing to `optimizer_ops.service_account_owners` or your configured external SA-owner table + column map. |
| 10 | **`v_principal_directory`** | `03_derived_views.sql` | Unified identity directory resolving humans and service accounts (`SA -> owner -> Director`) to accountable owners and Directors. |
| 11 | **`v_unmapped_service_accounts`** | `03_derived_views.sql` | Governance backlog of spending service accounts missing an owner or Director (`NO_OWNER_MAPPING` / `OWNER_HAS_NO_DIRECTOR`). |
| 12 | **`v_spend_by_director`** | `03_derived_views.sql` | Executive billing-aware spend (`on_demand_spend_usd` + `reservation_spend_usd`), slot-hours, and query rollup by Director, Department, and Project. |
| 13 | **`v_director_recommendations`** | `03_derived_views.sql` | Maps active table and query-family recommendations to responsible Directors, departments, and impacted reader counts. |
| 14 | **`v_pending_review`** | `04_change_sets.sql` | Active, unexpired, unsnoozed `PENDING_REVIEW` cards ordered by `net_monthly_value_usd DESC`. |
| 15 | **`v_approved_ready`** | `04_change_sets.sql` | `APPROVED` / `SCHEDULED` cards ready for `execute`, ordered by `score DESC`. |
| 16 | **`v_receipts`** | `04_change_sets.sql` | CFO proof ledger comparing `predicted_usd` vs. verified `realized_usd`. |

---

## 4. Master Script-to-Object Mapping Matrix (All 34 Objects)

| # | Object Name | Type | Created By | Populated / Maintained By |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `jobs_events` | Table | `sql/01_ops_schema.sql` | `02_collector_run.sql` + `remote_collector.py` (`1b_query_previews`) |
| 2 | `jobs_hourly_slots` | Table | `sql/01_ops_schema.sql` | `02_collector_run.sql` + `remote_collector.py` |
| 3 | `table_state_daily` | Table | `sql/01_ops_schema.sql` | `02_collector_run.sql` + `remote_collector.py` (multi-project/region) |
| 4 | `columns_daily` | Table | `sql/01_ops_schema.sql` | `02_collector_run.sql` + `remote_collector.py` |
| 5 | `table_partitions_daily` | Table | `sql/01_ops_schema.sql` | `02_collector_run.sql` + `remote_collector.py` |
| 6 | `dataset_state_daily` | Table | `sql/01_ops_schema.sql` | `02_collector_run.sql` + `remote_collector.py` + `collector_driver.py` |
| 7 | `object_state_raw_daily` | Table | `sql/01_ops_schema.sql` | `02_collector_run.sql` + `remote_collector.py` + `collector_driver.py` |
| 8 | `recommender_recommendations` | Table | `sql/01_ops_schema.sql` | `02_collector_run.sql` + `remote_collector.py` |
| 9 | `recommender_insights` | Table | `sql/01_ops_schema.sql` | `02_collector_run.sql` + `remote_collector.py` |
| 10 | `collector_audit` | Table | `sql/01_ops_schema.sql` | `02_collector_run.sql` + `remote_collector.py` + `collector_driver.py` |
| 11 | `collector_config` | Table | `sql/01_ops_schema.sql` | `sql/01_ops_schema.sql` (idempotent `MERGE` seed) |
| 12 | `employee_hierarchy` | Table | `sql/01_ops_schema.sql` | `sql/01_ops_schema.sql` (or external `employee_hierarchy_table`) |
| 13 | `service_account_owners` | Table | `sql/01_ops_schema.sql` | Operators (or external `service_account_owner_table`) |
| 14 | `regions_seen` | Table | `sql/01_ops_schema.sql` | `optimizer/regions.py` (Cloud Asset Inventory discovery) |
| 15 | `settings_changes` | Table | `sql/01_ops_schema.sql` | `optimizer/settings_store.py` (Review UI `/#settings`) |
| 16 | `change_sets` | Table | `sql/04_change_sets.sql` | `compiler.py`, `store.py`, `review_app/`, `executor/`, `verifier.py` |
| 17 | `rule_accuracy` | Table | `sql/04_change_sets.sql` | `optimizer/verifier.py` |
| 18 | `cost_watchdogs` | Table | `sql/04_change_sets.sql` | `optimizer/executor/class2.py` + `optimizer/verifier.py` |
| 19 | `v_config` | View | `sql/03_derived_views.sql` | Dynamic view over `collector_config` |
| 20 | `v_reservation_rates` | View | `sql/03_derived_views.sql` | Dynamic view over `object_state_raw_daily` + `v_config` |
| 21 | `v_jobs_costed` | View | `sql/03_derived_views.sql` | Dynamic view over `jobs_events` + `v_reservation_rates` + `v_config` |
| 22 | `v_table_read_write_90d` | View | `sql/03_derived_views.sql` | Dynamic view over `v_jobs_costed` |
| 23 | `v_query_families_28d` | View | `sql/03_derived_views.sql` | Dynamic view over `v_jobs_costed` |
| 24 | `v_dataset_storage_billing_gap` | View | `sql/03_derived_views.sql` | Dynamic view over `table_state_daily` + `dataset_state_daily` + `v_config` |
| 25 | `v_unpartitioned_scan_targets` | View | `sql/03_derived_views.sql` | Dynamic view over `table_state_daily` + `columns_daily` + `v_table_read_write_90d` |
| 26 | `v_employee_hierarchy_src` | View | `03_derived_views.sql` / `attribution.py` | Shim over `employee_hierarchy` or custom HR table |
| 27 | `v_service_account_owners_src` | View | `03_derived_views.sql` / `attribution.py` | Shim over `service_account_owners` or custom SA-owner table |
| 28 | `v_principal_directory` | View | `sql/03_derived_views.sql` | Dynamic view over `v_employee_hierarchy_src` + `v_service_account_owners_src` |
| 29 | `v_unmapped_service_accounts` | View | `sql/03_derived_views.sql` | Dynamic view over `v_jobs_costed` + `v_principal_directory` |
| 30 | `v_spend_by_director` | View | `sql/03_derived_views.sql` | Dynamic view over `v_jobs_costed` + `v_principal_directory` |
| 31 | `v_director_recommendations` | View | `sql/03_derived_views.sql` | Dynamic view over `change_sets` + `v_jobs_costed` + `v_principal_directory` |
| 32 | `v_pending_review` | View | `sql/04_change_sets.sql` | Dynamic view over `change_sets` |
| 33 | `v_approved_ready` | View | `sql/04_change_sets.sql` | Dynamic view over `change_sets` |
| 34 | `v_receipts` | View | `sql/04_change_sets.sql` | Dynamic view over `change_sets` |
