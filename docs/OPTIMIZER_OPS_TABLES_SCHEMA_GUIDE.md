# 🗄️ BigQuery Optimization Control Plane: Schema & Tables Guide
*A Complete Reference to the **18 Tables** and **16 Views** in `optimizer_ops` Created by `init`*

---

## 🧭 Overview

When you run:
```bash
python3 -m optimizer.cli init -p <YOUR_PROJECT_ID>
```

The system creates (or non-destructively updates) the **`optimizer_ops`** dataset, initializes **18 persistent tables** and **16 analytical/queue views**, wires custom HR and service-account lookup shims (`optimizer/attribution.py`), and stamps a `schema_version` hash label on `v_jobs_costed` so `optimizer/schema_guard.py` can self-heal the schema automatically after code upgrades.

These assets are organized into **6 functional layers**:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│                         OPTIMIZER_OPS DATASET ARCHITECTURE (18 Tables · 16 Views)            │
├────────────────────────────┬──────────────────────────────┬──────────────────────────────────┤
│ 1. Telemetry Ingestion     │ 2. State & Metadata Layer    │ 3. Active Assist Ingest          │
│ • jobs_events              │ • table_state_daily          │ • recommender_recommendations    │
│ • jobs_hourly_slots        │ • columns_daily              │ • recommender_insights           │
│                            │ • table_partitions_daily     │                                  │
│                            │ • dataset_state_daily        │                                  │
│                            │ • object_state_raw_daily     │                                  │
├────────────────────────────┼──────────────────────────────┼──────────────────────────────────┤
│ 4. Governance & Queues     │ 5. Ops, Regions & Settings   │ 6. Org & Director Attribution    │
│ • change_sets              │ • collector_audit            │ • employee_hierarchy             │
│ • rule_accuracy            │ • collector_config           │ • service_account_owners         │
│ • cost_watchdogs           │ • regions_seen               │ • v_employee_hierarchy_src       │
│ • v_pending_review         │ • settings_changes           │ • v_service_account_owners_src   │
│ • v_approved_ready         │ • v_config                   │ • v_principal_directory          │
│ • v_receipts               │ • v_reservation_rates        │ • v_unmapped_service_accounts    │
│                            │ • v_jobs_costed (+ 4 views)  │ • v_spend_by_director (+ 1 view) │
└────────────────────────────┴──────────────────────────────┴──────────────────────────────────┘
```

---

## 1. Query & Event Telemetry Layer

### 1.1 `optimizer_ops.jobs_events`
* **What it does:** Stores a long-term archive of BigQuery jobs and queries (`MERGE`-keyed on `region, project_id, job_id`, partitioned by `DATE(creation_time)` and clustered by `project_id, query_hash`) across your project or entire GCP Organization and across all discovered regions.
* **Why it's important:**
  * Google's native `INFORMATION_SCHEMA.JOBS` / `JOBS_BY_ORGANIZATION` retains history for only **180 days**. `jobs_events` preserves query telemetry beyond 180 days (first run backfills 180 days; subsequent runs perform a fast 3-day incremental `MERGE` with watermark overlap).
  * **Organization-Wide Scope & Automatic SQL Hydration:** In `JOBS_BY_ORGANIZATION` mode (`--org` or UI setting), queries from every project in the GCP Organization land centrally in `jobs_events`. Because BigQuery omits `query` text from `JOBS_BY_ORGANIZATION`, collector step `1b_query_previews` (`optimizer/remote_collector.py`) automatically hydrates normalized 1 KB `query_preview` snippets per `(project_id, query_hash)` from per-project `JOBS_BY_PROJECT` views so clustering filter extraction and Class 4 SQL anti-pattern detection work org-wide.
  * **Privacy-Safe:** Captures normalized `query_hash` and a truncated 1 KB `query_preview` rather than unbounded raw SQL payloads.

### 1.2 `optimizer_ops.jobs_hourly_slots`
* **What it does:** Aggregates CPU slot consumption (`total_slot_ms`) and concurrency (`jobs`) by `(region, hour_ts, project_id, reservation_id)` from `JOBS_TIMELINE` / `JOBS_TIMELINE_BY_ORGANIZATION`.
* **Why it's important:** Maps hourly concurrency curves (peak daytime bursts vs. overnight ETL baselines) to power **`W-01` Editions Reservation Sizing** using BigQuery's real 50-slot, 1-minute autoscaling billing math.

---

## 2. Multi-Region & Cross-Project State Snapshot Layer (Daily)

All five daily snapshot tables include `region` and `project_id` columns and automatic `partition_expiration_days` so `optimizer_ops` never becomes a storage-hygiene finding itself. `sql/02_collector_run.sql` collects the host project in the home location, while `optimizer/remote_collector.py` collects sibling projects in the home location plus all discovered projects across remote regions via short-lived 24-hour `_stg_*` courier tables.

### 2.1 `optimizer_ops.table_state_daily` *(400-day partition expiration)*
* **What it does:** Daily snapshot of table storage metrics: logical bytes (total, active, long-term), physical compressed bytes (active, long-term, time-travel, fail-safe), row count, partition count, `require_partition_filter`, and current DDL. Queries `TABLE_STORAGE_BY_ORGANIZATION` first with automatic per-project `TABLE_STORAGE` fallback and fault isolation.
* **Why it's important:** Powers cold/unused table detection (`C3-05`), logical-vs-physical compression math (`C1-05`), and physical time-travel window tuning (`C1-04`).

### 2.2 `optimizer_ops.columns_daily` *(90-day partition expiration)*
* **What it does:** Records every column (`column_name`, `data_type`, `is_partitioning_column`, `clustering_ordinal_position`) across discovered projects and regions.
* **Why it's important:** Identifies temporal partition candidates (`C3-01`), clustering candidates (`C1-01`), and explicit projection lists for `SELECT *` rewrites (`C4-01`).

### 2.3 `optimizer_ops.table_partitions_daily` *(120-day partition expiration)*
* **What it does:** Captures partition-level row counts, logical bytes, `last_modified`, and storage tier (`ACTIVE` / `LONG_TERM`).
* **Why it's important:** Detects micro-partition sprawl nearing BigQuery's 10,000-partition limit (`C3-03`) and partition skew.

### 2.4 `optimizer_ops.dataset_state_daily` *(400-day partition expiration)*
* **What it does:** Captures dataset locations, `SCHEMATA_OPTIONS` (default table/partition expirations, `max_time_travel_hours`), and `storage_billing_model` (`LOGICAL` vs. `PHYSICAL`, backfilled across projects via the BigQuery Datasets API in `optimizer/collector_driver.py`).
* **Why it's important:** Flags scratch/staging datasets missing default expirations (`C1-03`) and datasets where switching to physical storage cuts monthly storage bills (`C1-05`).

### 2.5 `optimizer_ops.object_state_raw_daily` *(400-day partition expiration)*
* **What it does:** Schema-drift-proof raw JSON (`raw_json`) for `MATERIALIZED_VIEW`, `VIEW`, `TABLE_CONSTRAINT`, `RESERVATION`, `ASSIGNMENT`, `CAPACITY_COMMITMENT`, and Data Transfer Service `SCHEDULED_QUERY` configs.
* **Why it's important:** Supplies real reservation edition & commitment rates (`v_reservation_rates`), PK/FK constraint posture (`C1-10`), zombie scheduled query detection (`C1-08`), and writer quiescing during Class 3 swaps.

---

## 3. Google Active Assist Native Ingestion Layer

### 3.1 `optimizer_ops.recommender_recommendations`
* **What it does:** Pulls recommendations from Google Cloud Active Assist (`PartitionClusterRecommender`, `materializedview.Recommender`) via `RECOMMENDATIONS_BY_ORGANIZATION` / `RECOMMENDATIONS`.
* **Why it's important:** Lets the scoring engine audit Google's native estimates against actual table spend (`native_cap_scan_reduction`), discount subquery-summation inflation (`d_summation`), and write state changes (`CLAIMED`, `SUCCEEDED`, `DISMISSED`) back to Active Assist.

### 3.2 `optimizer_ops.recommender_insights`
* **What it does:** Stores supporting diagnostic payloads and observation windows from `INFORMATION_SCHEMA.INSIGHTS`.
* **Why it's important:** Grounds card evidence windows and `d_window` confidence discounts.

---

## 4. Recommendation Store, Governance & Learning Layer (`sql/04_change_sets.sql`)

### 4.1 `optimizer_ops.change_sets` *(The Heart of the Control Plane)*
* **What it does:** Master state-machine table holding every compiled recommendation card, target coordinates (`target_project`, `target_dataset`, `target_table`, `target_region`), proposed DDL / PR payload, billing-aware `evidence_json`, confidence factors, FinOps & two-person `approvals`, `state_history`, `rollback_plan_json`, `progress_json`, and verified savings receipts.
* **Lifecycle States:**
  * **Direct Guarded (Class 1–3):** `DETECTED` $\to$ `PENDING_REVIEW` $\to$ `APPROVED` $\to$ `APPLYING` $\to$ `APPLIED` $\to$ `VERIFYING` $\to$ `VERIFIED` *(or `REGRESSED` $\to$ `ROLLING_BACK` $\to$ `ROLLED_BACK` with 90-day anti-looping `snooze_until`)*
  * **Code / PR Route (Class 4):** `PENDING_REVIEW` $\to$ `APPROVED` $\to$ `PR_HANDED_OFF` (`optimizer/executor/pr_handoff.py` generates branch + SQL diff + `gh pr create` command) $\to$ **Mark PR merged** in UI $\to$ `APPLIED` $\to$ `VERIFYING` $\to$ `VERIFIED`
  * **Queue Hygiene:** Unsigned money-losing cards below `min_card_net_monthly_usd` (`$0` default) and stale legacy estimates are automatically retired as `SUPERSEDED`; stale cards expire via `EXPIRED`.

### 4.2 `optimizer_ops.rule_accuracy`
* **What it does:** Tracks `applies` and rolling `realized_over_predicted` accuracy per `rule_id`.
* **Why it's important:** Feeds the `d_history` confidence multiplier (`0.25–1.25`) once verified post-window measurements complete.

### 4.3 `optimizer_ops.cost_watchdogs`
* **What it does:** Monitors recurring-cost Class 2 objects (`MV_REFRESH`, `INDEX_STORAGE`) against `monthly_limit_usd`.
* **Why it's important:** Ensures Smart-Tuned Materialized Views (`max_staleness = INTERVAL '4' HOUR`) and Search Indexes never cost more to maintain than they save.

---

## 5. Ops, Region Discovery & In-App Settings Layer

### 5.1 `optimizer_ops.collector_audit`
* **What it does:** Logs every collector step per region (`run_id`, `at`, `step`, `region`, `status`, `row_count`, `message`).
* **Why it's important:** Full operational observability across multi-region and cross-project collection runs.

### 5.2 `optimizer_ops.collector_config`
* **What it does:** Key-value table of pricing constants (`on_demand_usd_per_tib = 6.25`, logical/physical storage rates, Standard/Enterprise/Enterprise Plus/1-yr commitment slot-hour rates, candidate byte thresholds).
* **Why it's important:** Keeps raw telemetry in bytes/slot-ms while allowing contractual rate overrides in one table.

### 5.3 `optimizer_ops.regions_seen`
* **What it does:** Records every BigQuery location discovered by Cloud Asset Inventory (`optimizer/regions.py`) or configured statically: `region`, `first_seen_at`, `last_seen_at`, `dataset_count`, `project_count`, `included`, `source` (`asset_inventory` | `config`), and `is_new_alert`.
* **Why it's important:** Powers zero-touch multi-region discovery (`regions: auto`), surfaces **"New region detected"** alerts in the UI when datasets appear in a new region (catching unreserved regional sprawl), and serves as a cached fallback if Cloud Asset Inventory is temporarily unreachable.

### 5.4 `optimizer_ops.settings_changes`
* **What it does:** Append-only audit table storing every setting edited in `<app-url>/#settings` (`change_id`, `key`, `action` = `SET` | `RESET`, `value_json`, `previous_value_json`, `previous_override_json`, `changed_by`, `changed_at`, `note`), plus the settings-editor roster (`_access.settings_editors`).
* **Why it's important:** Lets Settings Admins, Settings Editors, and FinOps Approvers tune everyday settings in the browser (regions, query scope, lookup tables + column dropdowns, Gemini model, savings thresholds) with instant validation, 1-click **Undo**, and automatic pickup by the nightly Cloud Run Job (`optimizer/cli.py`).

---

## 6. Organizational Hierarchy & Service-Account Attribution Layer

### 6.1 `optimizer_ops.employee_hierarchy`
* **What it does:** Maps human `user_email` principals to `user_name`, `department`, `manager_email`, `director_email`, `director_name`, and `cost_center`.
* **Why it's important:** Connects query spend and table recommendations directly to engineering and business Directors. (Can be replaced with your own HR table via `employee_hierarchy_table` + `employee_hierarchy_columns` in `/#settings`.)

### 6.2 `optimizer_ops.service_account_owners`
* **What it does:** Maps automated `service_account_email` principals (`*.gserviceaccount.com`) to their accountable human `owner_email`, `owner_team`, `director_email`, `director_name`, and `application` (e.g., PowerBI, Looker, Airflow, dbt).
* **Why it's important:** Rolls automated BI and ETL spend up through `SA -> human owner -> Director` instead of leaving service accounts in an "Unassigned" bucket, and routes table ownership to real humans who can review cards. (Can be replaced with your own governance table via `service_account_owner_table` + `service_account_owner_columns` in `/#settings`.)

---

## 7. Analytical, Attribution & Queue Views (16 Views)

| # | View Name | Primary Function |
| :--- | :--- | :--- |
| 1 | **`v_config`** | Pivots `collector_config` pricing and byte-threshold constants for SQL joins. |
| 2 | **`v_reservation_rates`** | Resolves each reservation's edition (`STANDARD`, `ENTERPRISE`, `ENTERPRISE_PLUS`) & commitment rate (`$/slot-hour`) from `object_state_raw_daily`. |
| 3 | **`v_jobs_costed`** | Prices every non-`SCRIPT` query by actual `billing_mode` (`ON_DEMAND` bytes vs. `RESERVATION` slot-hours); holds `schema_guard`'s `schema_version` label. |
| 4 | **`v_table_read_write_90d`** | Computes 90-day read spend, slot-hours, scan counts, and DML writes per table, splitting multi-table queries proportionally across referenced tables. |
| 5 | **`v_query_families_28d`** | Aggregates 28-day query families by normalized `query_hash` with billing-mode spend breakdown. |
| 6 | **`v_dataset_storage_billing_gap`** | Computes compression ratios and net monthly savings from switching datasets between `LOGICAL` and `PHYSICAL` storage (`C1-05`). |
| 7 | **`v_unpartitioned_scan_targets`** | Flags unpartitioned tables (`>= 100 GiB`) with candidate `DATE`/`TIMESTAMP`/`DATETIME` columns and active scan spend (`C3-01`). |
| 8 | **`v_employee_hierarchy_src`** | Attribution shim pointing to `employee_hierarchy` or your configured external HR table. |
| 9 | **`v_service_account_owners_src`** | Attribution shim pointing to `service_account_owners` or your configured external SA-owner table. |
| 10 | **`v_principal_directory`** | Unified directory mapping both `HUMAN` and `SERVICE_ACCOUNT` emails to accountable owners, departments, and Directors. |
| 11 | **`v_unmapped_service_accounts`** | Lists active service accounts (90d) lacking an owner or Director, ranked by `est_spend_usd_90d DESC`. |
| 12 | **`v_spend_by_director`** | Executive billing-aware spend (`on_demand_spend_usd` + `reservation_spend_usd`), slot-hours, and query rollup by Director, Department, and Project. |
| 13 | **`v_director_recommendations`** | Joins active table and `query_hash` recommendations to responsible Directors, departments, and impacted reader counts. |
| 14 | **`v_pending_review`** | Active, unexpired, unsnoozed `PENDING_REVIEW` cards ordered by `net_monthly_value_usd DESC`. |
| 15 | **`v_approved_ready`** | Human-approved (`APPROVED` / `SCHEDULED`) change sets ready for `execute`. |
| 16 | **`v_receipts`** | CFO proof ledger comparing `predicted_usd` vs. verified `realized_usd`. |

---

## 📌 Complete Summary Table (All 34 Assets)

| Asset Name | Type | Layer | Primary Role |
| :--- | :--- | :--- | :--- |
| `jobs_events` | Table | Telemetry | Long-term query archive (>180d, multi-region, org-wide + hydrated SQL previews) |
| `jobs_hourly_slots` | Table | Telemetry | Hourly slot concurrency & `W-01` Editions sizing |
| `table_state_daily` | Table | Metadata | Daily multi-project/region table size, row count & physical/time-travel bytes |
| `columns_daily` | Table | Metadata | Daily column data types, partition & cluster keys across projects/regions |
| `table_partitions_daily` | Table | Metadata | Daily partition-level byte breakdown & active/long-term storage tier |
| `dataset_state_daily` | Table | Metadata | Daily dataset options & `LOGICAL`/`PHYSICAL` storage billing model |
| `object_state_raw_daily` | Table | Metadata | Raw JSON of MVs, views, PK/FK constraints, reservations & scheduled queries |
| `recommender_recommendations` | Table | Active Assist | Google Cloud native partition/cluster & MV recommendations |
| `recommender_insights` | Table | Active Assist | Supporting Active Assist insight payloads & observation windows |
| `collector_audit` | Table | Ops | Per-step, per-region collection health & row-count audit log |
| `collector_config` | Table | Ops | Google Cloud pricing rates & byte-threshold constants |
| `employee_hierarchy` | Table | Attribution | Built-in corporate hierarchy (`user_email` -> Director / Department) |
| `service_account_owners` | Table | Attribution | Built-in service-account ownership (`SA` -> human owner -> Director) |
| `regions_seen` | Table | Regions | Discovered BigQuery locations, dataset/project counts & new-region alerts |
| `settings_changes` | Table | Settings | Append-only audit log for UI-edited settings, editors ACL & 1-click Undo |
| `change_sets` | Table | Governance | **Core Recommendation, Approval, Rollback & Receipt State Machine** |
| `rule_accuracy` | Table | Governance | Closed-loop accuracy tracker (`realized_over_predicted` -> `d_history`) |
| `cost_watchdogs` | Table | Governance | Recurring-cost guardrails for Class 2 MVs & Search Indexes |
| `v_config` | View | Pricing | Typed single-row pivot of `collector_config` |
| `v_reservation_rates` | View | Pricing | Per-reservation edition & commitment `$/slot-hour` rates |
| `v_jobs_costed` | View | Pricing | Billing-aware per-job dollar cost (`ON_DEMAND` vs `RESERVATION`) + `schema_version` |
| `v_table_read_write_90d` | View | Analytics | Proportional 90-day read spend, slot-hours & DML writes per table |
| `v_query_families_28d` | View | Analytics | Normalized 28-day `query_hash` families for MVs, Class 4 & baselines |
| `v_dataset_storage_billing_gap` | View | Analytics | Logical vs. physical dataset storage savings calculator (`C1-05`) |
| `v_unpartitioned_scan_targets` | View | Analytics | Unpartitioned high-scan tables with temporal columns (`C3-01`) |
| `v_employee_hierarchy_src` | View | Attribution | Configurable shim over built-in or custom HR hierarchy table |
| `v_service_account_owners_src` | View | Attribution | Configurable shim over built-in or custom SA-owner table |
| `v_principal_directory` | View | Attribution | Unified human + service-account -> Director directory |
| `v_unmapped_service_accounts` | View | Attribution | 90-day spend backlog of unclaimed service accounts |
| `v_spend_by_director` | View | Executive | Billing-aware BigQuery spend & slot-hours rolled up by Director & Project |
| `v_director_recommendations` | View | Executive | Active table & query-family cards mapped to Directors & reader counts |
| `v_pending_review` | View | Queue | Active, unsnoozed `PENDING_REVIEW` cards for the Review UI |
| `v_approved_ready` | View | Queue | `APPROVED` / `SCHEDULED` cards ready for safe execution |
| `v_receipts` | View | Executive | Verified CFO savings receipts (`predicted_usd` vs `realized_usd`) |
