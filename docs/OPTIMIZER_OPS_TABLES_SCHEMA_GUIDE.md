# 🗄️ BigQuery Optimization Control Plane: Schema & Tables Guide
*A Complete Reference to the Tables and Views in `optimizer_ops` Created by `init`*

---

## 🧭 Overview

When you run:
```bash
python3 -m optimizer.cli init -p <YOUR_PROJECT_ID> -d customer360_telco
```

The system creates the **`optimizer_ops`** dataset and initializes **15 tables** and **11 derived views**. 

These assets form the backbone of the control plane and are organized into **6 logical functional layers**:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               OPTIMIZER_OPS DATASET ARCHITECTURE                       │
├──────────────────────────┬─────────────────────────────┬───────────────────────────────┤
│ 1. Telemetry Ingestion   │ 2. State & Metadata Layer   │ 3. Active Assist Ingest       │
│ • jobs_events            │ • table_state_daily         │ • recommender_recommendations │
│ • jobs_hourly_slots      │ • columns_daily             │ • recommender_insights        │
│                          │ • table_partitions_daily    │                               │
│                          │ • dataset_state_daily       │                               │
│                          │ • object_state_raw_daily    │                               │
├──────────────────────────┼─────────────────────────────┼───────────────────────────────┤
│ 4. Governance Ledger     │ 5. Audit & Pricing Config   │ 6. Org & Director Attribution │
│ • change_sets            │ • collector_audit           │ • employee_hierarchy          │
│ • rule_accuracy          │ • collector_config          │ • v_spend_by_director         │
│ • cost_watchdogs         │                             │ • v_director_recommendations  │
└──────────────────────────┴─────────────────────────────┴───────────────────────────────┘
```

---

## 1. Query & Event Telemetry Layer

### 1.1 `optimizer_ops.jobs_events`
* **What it does:** Stores a detailed, historical archive of every BigQuery job and query executed in your project or across your entire GCP Organization.
* **Why it's important:** 
  * Google's native `INFORMATION_SCHEMA.JOBS_BY_PROJECT` (and `JOBS_BY_ORGANIZATION`) only keeps query logs for **180 days** before permanently deleting them. 
  * `jobs_events` preserves this query telemetry indefinitely, allowing the Rules Engine to accurately measure 90-day scan history, query frequencies, and user access patterns.
  * **Organization-Wide Scope (`JOBS_BY_ORGANIZATION`):** When run in organization mode (`--org` or `RUN_BY_ORGANIZATION=True`), queries from all projects across the GCP Organization are ingested centrally into `jobs_events` with their initiating `user_email`, `project_id`, and table references.
  * **Privacy-Safe:** It only captures query hashes and the first 1 KB preview—protecting sensitive customer data while retaining query structure for analysis.

### 1.2 `optimizer_ops.jobs_hourly_slots`
* **What it does:** Aggregates CPU slot usage by region, project, and hour.
* **Why it's important:** 
  * It maps your hourly compute concurrency curves (identifying peak query hours vs. quiet weekend valleys).
  * This feeds the **Edition Sizing & Reservation Model** to determine whether moving from On-Demand to BigQuery Editions (Standard/Enterprise slot commitments) will save money.

---

## 2. Table State & Metadata Snapshot Layer (Daily)

### 2.1 `optimizer_ops.table_state_daily`
* **What it does:** Captures a daily snapshot of every table's storage footprint: total logical bytes, active vs. long-term storage, physical (compressed) bytes, row count, partition count, and current DDL.
* **Why it's important:** 
  * Identifies tables growing uncontrollably or tables that haven't had a single read/write in 90+ days (Rule `C3-05`).
  * Enables the calculation of exact storage compression ratios (Rule `C1-05`).

### 2.2 `optimizer_ops.columns_daily`
* **What it does:** Records every column in every table, including data types, whether it is a partitioning column, and clustering ordinal positions.
* **Why it's important:** 
  * Helps Rule `C3-01` instantly identify candidate temporal columns (`TIMESTAMP`, `DATE`, `DATETIME`) for unpartitioned tables.
  * Identifies missing partition filters without needing to scan actual table data.

### 2.3 `optimizer_ops.table_partitions_daily`
* **What it does:** Breaks down individual partitions within partitioned tables, logging rows, bytes, and active vs. long-term storage tiers.
* **Why it's important:** 
  * Detects "partition skew" (e.g. 90% of data landing in a single partition) and unpruned partition query anti-patterns.

### 2.4 `optimizer_ops.dataset_state_daily`
* **What it does:** Records dataset-level settings: creation dates, location, options, and the **`storage_billing_model`** (`LOGICAL` vs `PHYSICAL`).
* **Why it's important:** 
  * Identifies scratch/staging datasets (`staging_*`, `temp_*`) lacking table expiration dates (Rule `C1-03`).
  * Identifies datasets that can cut storage costs by 50%+ by switching to physical billing (Rule `C1-05`).

### 2.5 `optimizer_ops.object_state_raw_daily`
* **What it does:** Schema-drift-proof raw JSON storage for non-table objects: Materialized Views, standard Views, and Data Transfer Service / Scheduled Queries.
* **Why it's important:** 
  * Prevents write collisions during table restructuring by tracking which Scheduled Queries write to target tables.

---

## 3. Google Active Assist Native Ingestion Layer

### 3.1 `optimizer_ops.recommender_recommendations`
* **What it does:** Programmatically pulls raw recommendations directly from Google Cloud Active Assist (e.g., `google.bigquery.table.PartitionClusterRecommender`).
* **Why it's important:** 
  * Ingests Google's built-in ML recommendations so they can be filtered, capped against actual spend, and managed in one central place.

### 3.2 `optimizer_ops.recommender_insights`
* **What it does:** Captures the supporting telemetry and rationale generated by Google's recommendation models.
* **Why it's important:** Provides underlying diagnostic data to back up recommendation cards.

---

## 4. Recommendation Store, Governance & Learning Layer

### 4.1 `optimizer_ops.change_sets` *(The Heart of the Control Plane)*
* **What it does:** The primary ledger holding every generated recommendation card, proposed DDL, confidence breakdown, risk notes, human approval history, rollback plans, verified savings receipts, and post-mortem incident forensics.
* **Why it's important:** 
  * It serves as the single source of truth for the **Review Web UI**.
  * Tracks the full lifecycle of every optimization:
    $$\text{DETECTED} \rightarrow \text{PENDING\_REVIEW} \rightarrow \text{APPROVED} \rightarrow \text{APPLIED} \rightarrow \text{VERIFYING} \rightarrow \begin{cases} \text{VERIFIED} & \text{(ROI confirmed)} \\ \text{REGRESSED} \rightarrow \text{ROLLED\_BACK} & \text{(Instant reversion + 90d auto-snooze)} \end{cases}$$
  * Holds the **$0 Zero-Copy Rollback Plan** and preserves regressed tables under unique timestamps (`table__bqopt_regressed_YYYYMMDDHHMM`) for post-mortem forensics.
  * Enforces **Anti-Looping Suppression**: When a change set transitions to `ROLLED_BACK`, the engine records the post-mortem category (`rejection_reason` $\in$ {`REGRESSION_PERFORMANCE`, `REGRESSION_COST`, `PIPELINE_BREAK`, `DATA_MISMATCH`, `OTHER`}), incident note (`rejection_note`), and sets `snooze_until = CURRENT_TIMESTAMP() + 90 days`. This guarantees the rules engine will not repeatedly re-suggest the table while safely quarantined.
  * Supports **Human-in-the-Loop Re-evaluation**: Operators can un-snooze any quarantined change set via the Web UI (`[ 🔓 Un-snooze & Re-evaluate ]`) to return it to `PENDING_REVIEW` for parameter tuning or custom rejection.

### 4.2 `optimizer_ops.rule_accuracy`
* **What it does:** Continuously computes the historical accuracy ratio ($\frac{\text{Realized Savings}}{\text{Predicted Savings}}$) for each rule.
* **Why it's important:** 
  * Implements **Closed-Loop Machine Learning**: If a rule historically over-promises savings, its future confidence score ($d_{\text{history}}$) is automatically discounted, ensuring future recommendations become more accurate over time.

### 4.3 `optimizer_ops.cost_watchdogs`
* **What it does:** Monitors recurring-cost objects (like Materialized Views created under Class 2).
* **Why it's important:** 
  * Guarantees that automated Materialized Views never cost more in background refresh compute than the queries they accelerate. If spend exceeds `monthly_limit_usd`, it automatically alerts the team.

---

## 5. Audit & Configuration Layer

### 5.1 `optimizer_ops.collector_audit`
* **What it does:** An audit log that records every execution of the daily telemetry collector (step name, timestamp, rows ingested, execution time, and error messages).
* **Why it's important:** Guarantees full observability and troubleshooting if a permission or network issue occurs during nightly collection.

### 5.2 `optimizer_ops.collector_config`
* **What it does:** Key-value table storing official Google Cloud pricing constants (e.g., On-Demand $\$6.25/\text{TiB}$, Logical Storage $\$0.02/\text{GiB}$, Physical Storage $\$0.04/\text{GiB}$, Slot-hour rates).
* **Why it's important:** 
  * Decouples financial pricing from raw telemetry bytes. If Google updates pricing or if your organization has custom flat-rate contractual discounts, you update this table without altering historical data.

---

## 6. Organizational Hierarchy & Attribution Layer

### 6.1 `optimizer_ops.employee_hierarchy`
* **What it does:** Maps corporate identities (`user_email`) to individual employee names, managerial directors (`director_name`, `director_email`), vice presidents (`vp_name`), departments (`department`), and business cost centers (`cost_center`).
* **Why it's important:** 
  * Resolves the "Who owns this spend?" question. Instead of presenting raw project IDs or anonymous service accounts, telemetry is joined with the organizational hierarchy.
  * Enables executive reporting rolled up directly to VP and Director domains (e.g. Data Platform, Customer Analytics, Digital Operations).

---

## 7. Analytical & Queue Views

Along with the base tables, `init` creates **11 specialized SQL views**:

| View Name | Primary Function |
| :--- | :--- |
| **`v_pending_review`** | Filters active change sets ready for human review in the Web UI, stack-ranked by ROI score. |
| **`v_approved_ready`** | Queue of human-approved change sets ready for safe execution by the nightly executor. |
| **`v_receipts`** | Executive proof ledger comparing predicted savings vs. verified realized savings. |
| **`v_spend_by_director`** | **Executive spend rollup** aggregating monthly query scan spend, slot hours, and query volume by Director, VP, and Project. |
| **`v_director_recommendations`** | **Attributed recommendations** joining proposed optimizations to responsible Directors, departments, and impacted user counts. |
| **`v_config`** | Formats pricing constants for dynamic join operations. |
| **`v_jobs_costed`** | Converts raw bytes scanned and slot milliseconds into exact dollar costs per query. |
| **`v_table_read_write_90d`** | Computes 90-day read/write frequency and dollar scan spend per table. |
| **`v_query_families_28d`** | Normalizes queries by `query_hash` to establish pre-apply performance baselines. |
| **`v_dataset_storage_billing_gap`**| Identifies datasets with high compression where physical billing saves money. |
| **`v_unpartitioned_scan_targets`** | Flags unpartitioned tables with high scan volumes and candidate date columns. |

---

## 📌 Summary Table

| Table / View Name | Layer | Primary Role |
| :--- | :--- | :--- |
| `jobs_events` | Telemetry | Long-term query log archive (>180 days, Project or Org-wide) |
| `jobs_hourly_slots` | Telemetry | Hourly slot concurrency & Edition sizing |
| `table_state_daily` | Metadata | Daily table size, row count & storage tier snapshots |
| `columns_daily` | Metadata | Column data types, partition & cluster keys |
| `table_partitions_daily` | Metadata | Partition-level byte breakdown & storage tier |
| `dataset_state_daily` | Metadata | Dataset configuration & storage billing model |
| `object_state_raw_daily` | Metadata | Scheduled queries, MVs & view definitions |
| `employee_hierarchy` | Attribution | Corporate hierarchy (user email -> Director / Department) |
| `recommender_recommendations` | Active Assist | Ingestion of Google Cloud native recommendations |
| `recommender_insights` | Active Assist | Underlying ML insight rationale |
| `change_sets` | Governance | **Core Recommendation & Review Ledger** |
| `rule_accuracy` | Governance | Machine learning confidence tracker ($d_{\text{history}}$) |
| `cost_watchdogs` | Governance | Budget guardrails for Class 2 Materialized Views |
| `collector_audit` | Ops | Observability & collection health log |
| `collector_config` | Ops | Pricing constants & threshold parameters |
| `v_pending_review` | UI Queue | Active recommendation cards awaiting approval |
| `v_approved_ready` | Execution | Approved cards ready for overnight apply |
| `v_receipts` | Executive | Verified CFO ROI receipts ($/month) |
| `v_spend_by_director` | Executive | BigQuery spend & slot usage rollup by Director & Project |
| `v_director_recommendations` | Attribution | Recommendations mapped to Directors and impacted teams |
