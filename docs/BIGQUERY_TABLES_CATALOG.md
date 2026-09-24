# 📚 BigQuery Optimization Control Plane: Tables & Data Assets Catalog
*Comprehensive Reference for all Tables, Views, and Data Assets in `optimizer_ops`*

---

## 🎯 Executive Overview

When you run the initialization command:
```bash
python3 -m optimizer.cli init -p <project_id> -d <dataset_id>
```

The system creates the **`optimizer_ops`** dataset and executes three foundational DDL scripts:
1. **`sql/01_ops_schema.sql`**: Telemetry, snapshot tables, and pricing configurations.
2. **`sql/04_change_sets.sql`**: Recommendation queue, governance, learning tables, and queue views.
3. **`sql/03_derived_views.sql`**: Real-time analytical views that power the rules engine and verifier.

Below is the complete, easy-to-understand breakdown of **every table and view**, **what it does**, **why it is critical**, and **which script populates it**.

---

## 📊 Summary Architecture Map

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       OPTIMIZER_OPS DATASET                                             │
├───────────────────────────────────┬───────────────────────────────────┬─────────────────────────────────┤
│ 1. Raw Telemetry Layer            │ 2. Derived Intelligence Layer     │ 3. Action & Governance Layer    │
│    • jobs_events                  │    • v_jobs_costed                │    • change_sets                │
│    • jobs_hourly_slots            │    • v_table_read_write_90d       │    • rule_accuracy              │
│    • table_state_daily            │    • v_query_families_28d         │    • cost_watchdogs             │
│    • columns_daily                │    • v_dataset_storage_billing_gap│    • v_pending_review           │
│    • table_partitions_daily       │    • v_unpartitioned_scan_targets │    • v_approved_ready           │
│    • dataset_state_daily          │    • v_config                     │    • v_receipts                 │
│    • object_state_raw_daily       │                                   │                                 │
│    • recommender_recommendations  │                                   │                                 │
│    • collector_audit & config     │                                   │                                 │
└───────────────────────────────────┴───────────────────────────────────┴─────────────────────────────────┘
```

---

## 1. Raw Telemetry & State Capture Tables (From `sql/01_ops_schema.sql`)

### 1. `optimizer_ops.jobs_events`
* **What it is:** The permanent query execution archive. Stores query jobs, scanned bytes, slot milliseconds, query hashes, user emails, and cache hits.
* **Why it's important:** Standard BigQuery `INFORMATION_SCHEMA` deletes query history after 180 days. This table preserves your long-term query telemetry to detect seasonal query spikes, repeat scans, and expensive queries.
* **Populated by:** `sql/02_collector_run.sql` (Step 1: Ingests recent queries via `MERGE`).

### 2. `optimizer_ops.jobs_hourly_slots`
* **What it is:** Hourly rollups of slot consumption and query concurrency from `INFORMATION_SCHEMA.JOBS_TIMELINE`.
* **Why it's important:** Essential for slot sizing and determining whether your workload should move from On-Demand to Editions (Standard, Enterprise) commitments.
* **Populated by:** `sql/02_collector_run.sql` (Step 2: Hourly slot rollup).

### 3. `optimizer_ops.table_state_daily`
* **What it is:** Daily snapshot of all tables, recording row counts, partition counts, logical bytes, physical bytes, and partition filter settings.
* **Why it's important:** Tracks table storage growth and detects inactive tables that haven't been modified in 90+ days.
* **Populated by:** `sql/02_collector_run.sql` (Step 3: Table snapshot from `TABLE_STORAGE`).

### 4. `optimizer_ops.columns_daily`
* **What it is:** Snapshot of every column in your project, its data type, whether it is a partition key, and its clustering order.
* **Why it's important:** Enables the Rules Engine to find usable date/timestamp columns for partitioning and high-cardinality columns for clustering.
* **Populated by:** `sql/02_collector_run.sql` (Step 4: Column metadata from `COLUMNS`).

### 5. `optimizer_ops.table_partitions_daily`
* **What it is:** Snapshot of individual partition IDs, row counts, storage tiers (`ACTIVE` vs. `LONG_TERM`), and byte sizes.
* **Why it's important:** Detects partition skew (e.g., 90% of data in one day) and tables with too many small partitions.
* **Populated by:** `sql/02_collector_run.sql` (Step 5: Dynamic partition loops).

### 6. `optimizer_ops.dataset_state_daily`
* **What it is:** Daily snapshot of dataset options and the **Storage Billing Model** (`LOGICAL` vs. `PHYSICAL`).
* **Why it's important:** Identifies datasets that compress well and can save 50%+ on storage by switching to physical billing.
* **Populated by:** `sql/02_collector_run.sql` (Step 6) + `optimizer/collector_driver.py` (calls BigQuery Datasets REST API).

### 7. `optimizer_ops.object_state_raw_daily`
* **What it is:** Raw JSON capture of Materialized Views, regular Views, and Data Transfer Service **Scheduled Queries**.
* **Why it's important:** Allows the executor to automatically pause scheduled ETL queries during table rebuilds so they don't collide.
* **Populated by:** `sql/02_collector_run.sql` (Step 7) + `optimizer/collector_driver.py` (calls Data Transfer Service API).

### 8. `optimizer_ops.recommender_recommendations`
* **What it is:** Ingested recommendations from Google Active Assist (Partition/Cluster Recommenders, Idle Resource Recommenders).
* **Why it's important:** Programmatically pulls Google's native recommendations so they can be validated and executed with 1-click safety.
* **Populated by:** `sql/02_collector_run.sql` (Step 8: Recommender ingest).

### 9. `optimizer_ops.recommender_insights`
* **What it is:** Supporting insights and rationale behind Google Active Assist recommendations.
* **Why it's important:** Provides context and evidence displayed on recommendation cards in the Web UI.
* **Populated by:** `sql/02_collector_run.sql` (Step 8: Recommender insights).

### 10. `optimizer_ops.collector_audit`
* **What it is:** The ingestion health log. Records status (`OK`, `SKIPPED`, `ERROR`), timestamps, row counts, and error messages for every collection step.
* **Why it's important:** Complete operational observability to ensure the background collector runs reliably.
* **Populated by:** `sql/02_collector_run.sql` (Appends after every collection step).

### 11. `optimizer_ops.collector_config`
* **What it is:** Key-value table storing official Google Cloud pricing constants (e.g., $6.25/TiB on-demand, $0.02/GB logical storage, $0.06/slot-hour).
* **Why it's important:** Centralizes pricing rules so price updates never corrupt raw historical telemetry.
* **Populated by:** `sql/01_ops_schema.sql` (Seeded with official rates on Day 1).

---

## 2. Recommendation & Governance Tables (From `sql/04_change_sets.sql`)

### 12. `optimizer_ops.change_sets`
* **What it is:** The master control plane table. Stores every compiled recommendation, proposed DDL, evidence JSON, confidence score, approval history, and rollback plan.
* **Why it's important:** It is the central nervous system of the tool. Drives the Review Web UI, tracks human approvals, and guides the S0–S9 executor.
* **Populated by:** 
  * `optimizer/compiler.py` & `optimizer/store.py` (Inserts fresh cards when `rules` runs).
  * `review_app/main.py` (Updates approvals and rejection snooze intervals).
  * `optimizer/cli.py` (Updates state transitions: `APPLYING`, `APPLIED`, `VERIFYING`, `ROLLED_BACK`).

### 13. `optimizer_ops.rule_accuracy`
* **What it is:** Historical accuracy ledger tracking rolling `realized_over_predicted` ratios for each rule.
* **Why it's important:** Powers closed-loop machine learning ($d_{\text{history}}$ discount factor) so future recommendations become smarter based on past results.
* **Populated by:** `optimizer/verifier.py` (Updated when `verify` runs).

### 14. `optimizer_ops.cost_watchdogs`
* **What it is:** Active budget monitor for Class 2 recurring-cost objects (e.g., Materialized View refresh compute).
* **Why it's important:** Automatically alerts if an automated Materialized View refresh exceeds its approved monthly budget.
* **Populated by:** `optimizer/executor/class2.py` (Registered upon MV deployment) and monitored by `optimizer/verifier.py`.

---

## 3. Derived Analytical & Queue Views (From `sql/03_derived_views.sql` & `sql/04_change_sets.sql`)

| View Name | Source File | What It Does & Why It's Critical |
| :--- | :--- | :--- |
| **`v_config`** | `03_derived_views.sql` | Pivots `collector_config` key-values into columns for easy SQL joins. |
| **`v_jobs_costed`** | `03_derived_views.sql` | Calculates exact dollar costs for queries, enforcing billing honesty (On-Demand by bytes, Editions by slot-ms). |
| **`v_table_read_write_90d`** | `03_derived_views.sql` | Aggregates 90-day read costs and DML write activity per table. |
| **`v_query_families_28d`** | `03_derived_views.sql` | Groups queries by normalized `query_hash` (literals removed) to freeze baselines and detect latency regressions. |
| **`v_dataset_storage_billing_gap`** | `03_derived_views.sql` | Calculates compression ratios to find datasets that will save money by switching from Logical to Physical storage. |
| **`v_unpartitioned_scan_targets`** | `03_derived_views.sql` | Filters large unpartitioned tables that have high scan costs and candidate date columns. |
| **`v_pending_review`** | `04_change_sets.sql` | Feeds active, unsnoozed cards into the Review Web UI (`http://localhost:8080`). |
| **`v_approved_ready`** | `04_change_sets.sql` | Feeds approved cards into the nightly `execute` worker. |
| **`v_receipts`** | `04_change_sets.sql` | Displays executive proof of predicted vs. realized savings receipts. |

---

## 4. Master Script-to-Table Mapping Matrix

| Table / View Name | Type | Created By | Populated / Maintained By |
| :--- | :--- | :--- | :--- |
| `jobs_events` | Table | `sql/01_ops_schema.sql` | `sql/02_collector_run.sql` |
| `jobs_hourly_slots` | Table | `sql/01_ops_schema.sql` | `sql/02_collector_run.sql` |
| `table_state_daily` | Table | `sql/01_ops_schema.sql` | `sql/02_collector_run.sql` |
| `columns_daily` | Table | `sql/01_ops_schema.sql` | `sql/02_collector_run.sql` |
| `table_partitions_daily` | Table | `sql/01_ops_schema.sql` | `sql/02_collector_run.sql` |
| `dataset_state_daily` | Table | `sql/01_ops_schema.sql` | `02_collector_run.sql` + `collector_driver.py` |
| `object_state_raw_daily` | Table | `sql/01_ops_schema.sql` | `02_collector_run.sql` + `collector_driver.py` |
| `recommender_recommendations` | Table | `sql/01_ops_schema.sql` | `sql/02_collector_run.sql` |
| `recommender_insights` | Table | `sql/01_ops_schema.sql` | `sql/02_collector_run.sql` |
| `collector_audit` | Table | `sql/01_ops_schema.sql` | `sql/02_collector_run.sql` |
| `collector_config` | Table | `sql/01_ops_schema.sql` | `sql/01_ops_schema.sql` (Day 1 Seed) |
| `change_sets` | Table | `sql/04_change_sets.sql` | `optimizer/compiler.py`, `review_app/`, `cli.py` |
| `rule_accuracy` | Table | `sql/04_change_sets.sql` | `optimizer/verifier.py` |
| `cost_watchdogs` | Table | `sql/04_change_sets.sql` | `optimizer/executor/class2.py` |
| `v_pending_review` | View | `sql/04_change_sets.sql` | Dynamic view over `change_sets` |
| `v_approved_ready` | View | `sql/04_change_sets.sql` | Dynamic view over `change_sets` |
| `v_receipts` | View | `sql/04_change_sets.sql` | Dynamic view over `change_sets` |
| `v_config` | View | `sql/03_derived_views.sql` | Dynamic view over `collector_config` |
| `v_jobs_costed` | View | `sql/03_derived_views.sql` | Dynamic view over `jobs_events` + `v_config` |
| `v_table_read_write_90d` | View | `sql/03_derived_views.sql` | Dynamic view over `v_jobs_costed` |
| `v_query_families_28d` | View | `sql/03_derived_views.sql` | Dynamic view over `v_jobs_costed` |
| `v_dataset_storage_billing_gap` | View | `sql/03_derived_views.sql` | Dynamic view over `table_state_daily` + `v_config` |
| `v_unpartitioned_scan_targets` | View | `sql/03_derived_views.sql` | Dynamic view over `v_table_read_write_90d` |
