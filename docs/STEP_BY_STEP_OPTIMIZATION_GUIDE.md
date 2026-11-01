# 📘 BigQuery Optimization Control Plane: Step-by-Step Master Guide
## *From Step Zero to the Finish Line (Using `<YOUR_PROJECT_ID>.customer360_telco`)*

---

## 🧭 Table of Contents
1. [Executive Summary: How to Explain This in 60 Seconds](#1-executive-summary-how-to-explain-this-in-60-seconds)
2. [The 4 Core Gears (The Mental Model)](#2-the-4-core-gears-the-mental-model)
3. [Step 0: Prerequisites & Project Configuration](#step-0-prerequisites--project-configuration)
4. [Step 1: Initialize the Control Plane (`optimizer_ops`)](#step-1-initialize-the-control-plane-optimizer_ops)
5. [Step 2: Collect Telemetry & Query Metadata](#step-2-collect-telemetry--query-metadata)
6. [Step 3: Run the Rules Engine & Honest Scoring](#step-3-run-the-rules-engine--honest-scoring)
7. [Step 4: The Human-in-the-Loop Review Gate](#step-4-the-human-in-the-loop-review-gate)
8. [Step 5: Safe Execution with Production Guardrails](#step-5-safe-execution-with-production-guardrails)
9. [Step 6: Verify Realized Savings (The CFO Receipt)](#step-6-verify-realized-savings-the-cfo-receipt)
10. [Step 7: Rollback & Disaster Recovery](#step-7-rollback--disaster-recovery)
11. [Customer Production Rollout & Day-2 Automation](#11-customer-production-rollout--day-2-automation)

---

## 1. Executive Summary: How to Explain This in 60 Seconds

### The Problem in Every Enterprise:
> *"Every enterprise running BigQuery wants to reduce costs and speed up dashboards. Google Cloud provides Active Assist recommendations, but data engineering teams **rarely apply them in production**. Why? Because engineers are afraid of breaking Looker dashboards, dropping Row-Level Security (RLS) policies, or causing pipeline downtime."*

### What This Tool Does:
> *"The **BigQuery Optimization Control Plane** (`bq-optimizer`) is a production safety layer. It analyzes query logs, calculates honest dollar savings, gives engineers a 1-click web interface to review changes, executes updates with zero-downtime safety guardrails, and produces a verified ROI proof receipt for the CFO."*

---

## 2. The 4 Core Gears (The Mental Model)

```mermaid
graph TD
    A[Gear 1: The Observer<br/>Collects metadata & query logs] --> B[Gear 2: The Brain<br/>Rules engine & honest scoring]
    B --> C[Gear 3: The Gatekeeper<br/>Human-in-the-loop review UI]
    C --> D[Gear 4: Safe Execution & Receipt<br/>Multi-class executor & CFO receipt]
```

1. **Gear 1: The Observer (Collector)**: Reads BigQuery `INFORMATION_SCHEMA` and Active Assist. It **never reads customer payload data**—only query metadata, scan volumes, and execution times.
2. **Gear 2: The Brain (Rules Engine & Scoring)**: Evaluates 10+ optimization patterns (clustering, partitioning, unused table archiving, query anti-patterns) and calculates capped dollar savings.
3. **Gear 3: The Gatekeeper (Human Review UI)**: Formats recommendations as clear cards showing proposed SQL, risk ratings, and estimated savings. A human engineer must click **"Approve 👍"** before anything executes.
4. **Gear 4: The Executioner & Verifier**:
   - **Class 1 (Metadata)**: In-place `ALTER TABLE` DDL (0 downtime).
   - **Class 2 (Additive)**: Materialized Views with automated spend watchdogs.
   - **Class 3 (Table Rebuild)**: 10-stage Copy-Swap-Rebind with checksum validation and RLS preservation.
   - **Class 4 (Query Rewrite)**: Pull Request diffs for dbt models / Looker views.
   - **Verifier**: Measures post-change query scans vs. pre-change baseline and writes a CFO receipt.

---

## 3. Step 0: Prerequisites & Project Configuration

### Customer Environment Check:
- **Project ID**: `<YOUR_PROJECT_ID>`
- **Target Dataset**: `customer360_telco`
- **Tables Present**:
  - `dim_customer` (20,000 rows, 80+ wide columns, unpartitioned, unclustered)
  - `dim_location`, `dim_equipment`, `dim_product`
  - `fact_network_events` (1,307 rows)
  - `fact_subscriptions` (34,285 rows)
  - `potential_churn_customers` (51 rows)

### Configuration Options (1-Click Browser UI, Config File, or CLI Flags):
You have three flexible ways to configure your project, regions, and governance:

#### Option A: 1-Click Cloud Shell Install + In-App Settings (`/#settings` — Recommended)
Deploy via the **Open in Cloud Shell** button in [`README.md`](../README.md), then open `<your-app-url>/#settings` to:
- Run **Setup check** (14 read-only permission & region probes with copy-pasteable `gcloud` / `bq` fixes).
- Pick **regions**, **query history source** (`JOBS` vs `JOBS_BY_ORGANIZATION`), **HR employee hierarchy table**, **service-account owner table**, **schema columns**, and **Gemini model (`gemini-3.8-flash`)** from live dropdowns (saved to `optimizer_ops.settings_changes` with 1-click Undo).
- Click **▶ Run scan now** right in the browser.

#### Option B: Set defaults in `config.yaml`
```yaml
project_id: "<YOUR_PROJECT_ID>"
location: "US"
ops_dataset: "optimizer_ops"
regions: auto
asset_scope: "organizations/<YOUR_ORG_ID>"
jobs_view: "JOBS_BY_ORGANIZATION"   # or "JOBS"
demo_mode: false

pricing:
  reservation_savings_realization: 1.0

executor:
  enable_class1: true
  change_window_utc: [3, 9]
```

#### Option C: Pass dynamic CLI parameters on the fly (No YAML editing needed!)
You can pass flags directly to any CLI command to target specific projects, datasets, or locations:
```bash
# Target a specific dataset directly via -d / --dataset:
python -m optimizer.cli rules --dataset customer360_telco
python -m optimizer.cli execute --dataset customer360_telco

# Target another project directly via -p / --project:
python -m optimizer.cli init -p another-customer-project-id
python -m optimizer.cli rules -p another-customer-project-id -d analytics_mart

# Point to a custom config file via -c / --config:
python -m optimizer.cli rules --config /path/to/client_config.yaml
```

> **Note:** By default, if no `--dataset` is specified, `bq-optimizer` automatically scans and optimizes **all datasets** across the entire project!

---

## 4. Step 1: Initialize the Control Plane (`optimizer_ops`)

### What is Happening?
We create a dedicated, low-cost dataset called `optimizer_ops` inside the customer's project with **18 persistent tables** and **16 analytical/queue views** (see [`OPTIMIZER_OPS_TABLES_SCHEMA_GUIDE.md`](./OPTIMIZER_OPS_TABLES_SCHEMA_GUIDE.md)), automatically self-healed on code upgrades by `optimizer/schema_guard.py`:
1. `jobs_events` & `jobs_hourly_slots`: Multi-region, project/org-wide query history (with hydrated SQL previews) and hourly slot concurrency.
2. `table_state_daily`, `columns_daily`, `table_partitions_daily`, `dataset_state_daily`, `object_state_raw_daily`: Daily table, column, partition, dataset, reservation, and scheduled-query snapshots across all discovered projects & regions.
3. `employee_hierarchy` & `service_account_owners` (+ `v_principal_directory`, `v_unmapped_service_accounts`, `v_spend_by_director`): Human & service-account → Director spend attribution.
4. `regions_seen` & `settings_changes`: Cloud Asset Inventory region discovery (with new-region UI alerts) and append-only audit history for in-app `/#settings` edits.
5. `change_sets`, `rule_accuracy`, `cost_watchdogs`: Recommendation state machine, `d_history` learning, and Class 2 recurring-cost watchdogs.
6. `v_reservation_rates`, `v_jobs_costed`, `v_pending_review`, `v_approved_ready`, `v_receipts`: Billing-aware job pricing, Review UI queue, executor queue, and CFO proof receipts.

### How to Run:
```bash
cd <repo-root>

# 1. Initialize the dataset, tables, and views (idempotent)
python3 -m optimizer.cli init

# 2. Verify read/write permissions & discovered regions
python3 -m optimizer.cli check
```

**Terminal Output:**
```
[*] Initialized dataset: <YOUR_PROJECT_ID>.optimizer_ops (Location: US)
  + Ran DDL: sql/01_ops_schema.sql
  + Ran DDL: sql/04_change_sets.sql
  + Ran DDL: sql/03_derived_views.sql
✅ Control plane schema initialized successfully!
```

> **Tip:** If you want to automatically provision demo tables and seed realistic sample query history in one step, you can also run:
> ```bash
> ./scripts/demo_setup.sh
> ```

---

## 5. Step 2: Collect Telemetry & Query Metadata

### What is Happening?
The Collector script (`sql/02_collector_run.sql`) extracts query traces and table sizes from `INFORMATION_SCHEMA.JOBS_BY_PROJECT` and Google Active Assist into `optimizer_ops.jobs_events` and `table_state_daily`.

### How to Run Telemetry Collection:

#### Option A: Run On-Demand from the CLI
You can execute the collector script on demand with:
```bash
python3 -m optimizer.cli collect
```
*What this does:*
1. Runs `sql/02_collector_run.sql` against BigQuery `INFORMATION_SCHEMA` to populate query traces (`JOBS` or `JOBS_BY_ORGANIZATION`) and host-project snapshots into `optimizer_ops`.
2. In `JOBS_BY_ORGANIZATION` mode (where BigQuery omits SQL text), step `1b_query_previews` (`optimizer/remote_collector.py`) hydrates normalized 1 KB SQL previews per `(project_id, query_hash)` from per-project `JOBS_BY_PROJECT`.
3. Calls the Datasets & DTS APIs across discovered projects to backfill storage billing models (`LOGICAL` vs `PHYSICAL`) and scheduled queries.
4. Visits sibling projects in the home location plus every remote BigQuery location (`regions: auto` discovers locations & projects with Cloud Asset Inventory and records `optimizer_ops.regions_seen`), loading via short-lived 24h `_stg_*` courier tables and `MERGE`ing into `optimizer_ops`. Preview the route with `python3 -m optimizer.cli regions --no-record`, or run `python3 -m optimizer.cli scan` to run `collect` + `rules` in one read-only command.

#### Option B: Automated Nightly Collector (Production)
In customer production environments, `scripts/install.sh` automatically deploys `bqopt-nightly-pipeline` as a Cloud Run Job triggered nightly at **`03:00 UTC` (`0 3 * * *`)** by Cloud Scheduler (or, if using `terraform/main.tf` split jobs, add `collect = { args = ["-m", "optimizer.cli", "collect"], schedule = "0 3 * * *" }` to `locals.jobs`).

> **Why not a BigQuery Scheduled Query?** A scheduled query runs in a single location, so scheduling `sql/02_collector_run.sql` directly only collects the home location (where `optimizer_ops` lives). That's fine only if all of your datasets are in one location.

> **Note:** The `rules` command reads telemetry that is **already stored** in `optimizer_ops`. Running `collect` ensures your query and table logs are fresh before evaluating rules!

---

## 6. Step 3: Run the Rules Engine & Honest Scoring

### What is Happening?
The Rules Engine analyzes the query telemetry against 10+ optimization patterns:
1. **`C1-01` (Table Clustering)**: Detects frequent filtering on `loyalty_tier, persona` on `dim_customer`.
2. **`C4-01` (Projection Minimization)**: Detects expensive queries doing `SELECT *` across 80 columns of `dim_customer`.
3. **`C4-03` (SArgable Predicates)**: Flags queries using `WHERE DATE(customer_since_date) = ...` that prevent partition pruning.

### The Honest Scoring Formula:
The engine enforces the **Subquery-Summation Cap**:
$$\text{Max Table Savings} \le \text{Total 30-Day Query Spend on Table}$$
*A recommendation can never promise more savings than the actual dollars spent on that table.*

### How to Run:
```bash
python3 -m optimizer.cli rules
```

**Terminal Output:**
```
expired=0 findings=4 inserted=4 suppressed=0
```

---

## 7. Step 4: The Human-in-the-Loop Review Gate

### What is Happening?
The customer opens the web browser to inspect the recommendations. **Nothing is modified automatically in production without human approval.**

### How to Run:
```bash
python3 review_app/main.py --port 8080
```
Open **`http://localhost:8080`** in your browser.

### What the Customer Sees:
Each recommendation card presents an unambiguous contract:
1. **Target Resource**: `<YOUR_PROJECT_ID>.customer360_telco.dim_customer`
2. **Action**: `SET_CLUSTERING` on `(loyalty_tier, persona, customer_id)`
3. **Projected Monthly Savings**: `$76.29 / mo`
4. **Safety Class**: `Class 1 (In-Place DDL, Zero Downtime)`
5. **Exact SQL DDL**:
   ```sql
   ALTER TABLE `<YOUR_PROJECT_ID>.customer360_telco.dim_customer`
   SET OPTIONS (clustering_fields = ['loyalty_tier', 'persona', 'customer_id']);
   ```

### Taking Action:
- Operators can filter cards across **Min Value**, **Project**, **Dataset**, **Region** (dropdown or clickable pills in the top `RegionsBar`), **Owner / Director**, **Confidence**, and **Class**, or ask questions in **Ask Gemini** (`gemini-3.8-flash`).
- When the engineer clicks **"Approve 👍"**:
  - The control plane **freezes a pre-execution performance baseline** (last 28/30-day per-family query metrics).
  - Transitions state from `PENDING_REVIEW` $\to$ `APPROVED`.

---

## 8. Step 5: Safe Execution with Production Guardrails

### What is Happening?
When `execute` is called, the executor processes all `APPROVED` change sets (dispatching DDL in each asset's `target_region`):
1. **Maintenance Window Check (`change_window_utc`)**: Ensures current UTC hour is inside the approved change window (`03:00–09:00 UTC` by default). If `execute` is invoked outside the window, `OutsideChangeWindow` safely **defers** `APPROVED` cards for the next window rather than failing them.
2. **Rate Limiting**: Enforces a maximum of 1 structural change per table per 7 days.
3. **Execution Dispatch**:
   - **Class 1 (In-Place DDL)**: Runs `ALTER TABLE` / `ALTER SCHEMA` DDL directly with **zero downtime**.
   - **Class 2 (Additive)**: Creates Smart-Tuned Materialized Views (`max_staleness = INTERVAL '4' HOUR`) or Search Indexes and registers `cost_watchdogs`.
   - **Class 3 (Structural Rebuild)**: Runs the 10-stage Copy-Swap-Rebind state machine (`S0`–`S9`): checks streaming buffer, pauses DTS writers, preserves IAM bindings/RLS policies, validates `FARM_FINGERPRINT` checksums, executes an atomic 3-way table rename, and provides instant rollback.
   - **Class 4 (Query Refactor — `pr_handoff.py`)**: Transitions card to `PR_HANDED_OFF` with a Git branch, rewritten SQL file, and `gh pr create` command; clicking **Mark PR merged** in the UI transitions it to `APPLIED` $\to$ `VERIFYING`.
4. **Active Assist Sync**: Marks the recommendation in Google Cloud Active Assist as `CLAIMED` / `SUCCEEDED`.

### How to Run:
```bash
python3 -m optimizer.cli execute
```

**Terminal Output:**
```
[*] Executing change set f4a74acc-ad8 on customer360_telco.dim_customer...
  + Validated maintenance window: OPEN
  + Applied Class 1 DDL: ALTER TABLE `<YOUR_PROJECT_ID>.customer360_telco.dim_customer` SET OPTIONS (clustering_fields = ['loyalty_tier', 'persona', 'customer_id']);
  + Synced Active Assist recommendation to SUCCEEDED
  + State updated to VERIFYING
```

---

## 9. Step 6: Verify Realized Savings (The CFO Receipt)

### What is Happening?
The verification engine (`python -m optimizer.cli verify`) compares query performance during the 14-day post-execution window against the frozen baseline (demo configs set `verify_window_days: 0`, which verifies instantly for regressions but leaves `realized_usd` as `null`/pending until real post-apply telemetry exists — the verifier never substitutes the prediction for the measurement):
- **Regression Watchdog**: If queries scan *more* data than before (>15% regression), the watchdog flags an alert.
- **Success Proof Receipt**: If queries scan less data, the engine calculates:
  $$\text{Realized Savings (\$/mo)} = (\text{Baseline Bytes} - \text{Post-Apply Bytes}) \times \text{Query Frequency} \times \text{Rate}$$
- Writes a permanent receipt to `optimizer_ops.v_receipts`.
- Updates `rule_accuracy` table so future recommendations are even more accurate.

### How to Run:
```bash
python3 -m optimizer.cli verify
```

### Inspecting the CFO Proof Receipt in BigQuery:
```sql
SELECT 
    change_set_id,
    target_dataset,
    target_table,
    predicted_usd,
    realized_usd,
    state,
    applied_at
FROM `<YOUR_PROJECT_ID>.optimizer_ops.v_receipts`;
```

---

## 10. Step 7: Rollback & Disaster Recovery

If an applied change ever needs to be reverted:
```bash
python3 -m optimizer.cli rollback --id <change_set_id>
```
*What happens under the hood:*
- For Class 1: Drops the clustering / partition filter option.
- For Class 3: Swaps the backup clone back into production and restores previous IAM/RLS policies.

---

## 11. Customer Production Rollout & Day-2 Automation

To run this continuously in a customer environment without manual terminal intervention:

### Architecture for Automation:

#### 🟢 Day 1 (1-Click Cloud Shell Setup or CLI `init`):
Click **Open in Cloud Shell** in [`README.md`](../README.md) (which runs `scripts/install.sh` automatically) — or run `python -m optimizer.cli init` manually — to create `optimizer_ops` (**18 tables + 16 views**), deploy the IAP-protected Review UI, and wire the **▶ Run scan now** button on `<app-url>/#settings`.

#### 🔄 Nightly Schedule (Automated on Cloud Scheduler + Cloud Run Jobs):
`scripts/install.sh` provisions a unified nightly Cloud Run Job (`bqopt-nightly-pipeline`) triggered by Cloud Scheduler (`bqopt-nightly-schedule`) at **`03:00 UTC` (`0 3 * * *`)** — right inside the default `change_window_utc: [3, 9]` (`03:00–09:00 UTC`) maintenance window:

```bash
# Runs sequentially inside bqopt-nightly-pipeline at 03:00 UTC (loads saved /#settings overrides first):
python -m optimizer.cli pipeline
#   Step 1/4: collect  (multi-region + multi-project telemetry & hydrated SQL previews)
#   Step 2/4: rules    (14 infra/billing rules + Tri-Engine Class 4 + money-losing filter)
#   Step 3/4: execute  (applies human-APPROVED cards inside 03:00-09:00 UTC; Class 4 -> PR_HANDED_OFF)
#   Step 4/4: verify   (checks Day-14 post-apply receipts + daily MV cost watchdogs)
```

*(Alternatively, `terraform/main.tf` supports splitting `collect`, `rules`, `execute` (`30 3 * * *`), `verify` (`0 7 * * *`), and `sync-recommender` into separate Cloud Scheduler jobs.)*

3. **Day-to-Day Browser Workflow**:
   Engineers and FinOps leads open `https://<your-cloud-run-iap-url>` to filter cards by **Project / Dataset / Region / Owner**, tune settings in `/#settings`, trigger on-demand scans, and approve changes.

---

## 🔄 Resetting or Cleaning Up Demo Assets

- **Standard Reset (clears demo cards and workload tables, keeps ops dataset)**:
  ```bash
  ./scripts/demo_cleanup.sh
  ```
- **100% Full Teardown (wipes everything including `optimizer_ops`)**:
  ```bash
  ./scripts/demo_cleanup.sh --wipe-ops
  ```

---

## 🎯 Summary Checklist for Presenting to Customer

| Step | CLI Command | Customer Value |
| :--- | :--- | :--- |
| **1. Initialize** | `python -m optimizer.cli init` | Isolated, zero data leakage, no third-party SaaS required. |
| **2. Observe** | Ingest `INFORMATION_SCHEMA` | Zero overhead on production workloads; reads metadata only. |
| **3. Score** | `python -m optimizer.cli rules` | Honest savings math (Subquery-Summation Cap prevents inflated claims). |
| **4. Review** | `python review_app/main.py` | Complete human control; frozen performance baselines. |
| **5. Execute** | `python -m optimizer.cli execute` | Zero-downtime metadata changes & guarded atomic table rebuilds. |
| **6. Prove** | `python -m optimizer.cli verify` | Realized dollar proof receipts closed with audit tracking. |
