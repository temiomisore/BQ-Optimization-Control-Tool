# 🎬 BigQuery Optimization Control Plane: Master Demo Playbook 2
*Advanced Technical Field Guide for Enterprise Additions: Two-Person Governance, Dynamic Honest Math, Storage Receipts, Streaming Buffer Cutovers & Extended Rules Catalog*

---

## 🌟 1. Executive Storyline (Your Opening 60 Seconds)

> *"In our first master demo, we demonstrated foundational clustering, partitioning, and query refactoring without downtime.
> 
> Today, in **Demo 2**, we address the most complex, politically sensitive operational requirements in enterprise BigQuery lakehouses:
> 
> 1. **Dynamic Honest Math Across Any Table**: Native cloud recommenders routinely promise $50,000/month in savings by evaluating queries in isolation. Our engine enforces **dynamic workload-capped scoring** across every table in your enterprise—capping savings to real audited read spend and applying mathematical multi-stage overlap discounts with zero hardcoded table rules.
> 2. **Enterprise Two-Person Approval Governance**: High-impact structural table rebuilds (Class 3) cannot be executed by a single engineer clicking a button. We mandate a cryptographic **two-person sign-off workflow**: the verified Data Asset Owner must approve first, followed by Platform/FinOps before execution is unlocked.
> 3. **Automated Ownership Attribution**: Who owns each table? Our multi-tiered attribution engine resolves asset ownership in priority order: from BigQuery resource labels to Git codeowners, top active writing service accounts, and dataset IAM admins.
> 4. **Graceful Streaming Buffer Cutovers**: Altering partitioned tables while Apache Kafka, Pub/Sub, or Datastream are continuously inserting rows causes silent data loss in traditional tools. Our copy-swap executor checks the streaming buffer in real-time, safely draining or circuit-breaking before any atomic rename.
> 5. **Immutable Storage Receipts & Extended Rule Catalog**: From physical storage time-travel windows and history-based adaptive optimizations to BigQuery Search Indexes with runaway cost watchdogs and zombie scheduled query pauses—we provide closed-loop proof of realized dollar savings."*

---

## 🗺️ 2. Master Design Document Mapping Matrix (Demo 2 Additions)

| Optimization Class / Mechanism | Rule / Feature ID | Design Doc Section | Real-World Remediation | Live Demo Target Asset | Execution Mode |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Scoring Engine** | `HONEST_SCORING` | **Design Doc §6.2** | Dynamic Table Spend Cap ($8k claim capped to $544/mo) | `demo_ecommerce.product_catalog` | `DYNAMIC_AUDIT` |
| **Governance Engine** | `OWNERSHIP` | **Design Doc §8 & §9.1** | Multi-Tier Attribution (Label $\to$ Codeowners $\to$ Writer $\to$ Admin) | `demo_ecommerce.high_churn_events` | `AUTO_RESOLVE` |
| **Governance Engine** | `TWO_PERSON_APPROVAL` | **Design Doc §8** | Mandatory 2-Key Sign-off (Owner + Platform) | `demo_ecommerce.iot_sensor_telemetry` | `HUMAN_IN_LOOP` |
| **Execution Engine** | `STREAMING_CUTOVER` | **Design Doc §9.4 S2b** | Zero-Loss Streaming Buffer Drain Cutover | `demo_ecommerce.streaming_events_active` | `DRAIN_GUARDED` |
| **Verification Engine** | `STORAGE_RECEIPTS` | **Design Doc §10** | Realized Storage Savings Receipts ($/GiB Baseline) | `demo_ecommerce.high_churn_events` | `CFO_RECEIPT` |
| **Class 1 (In-Place Metadata)** | `C1-04` | **Design Doc §5.1** | Time-Travel Window Tuning (168h $\to$ 48h) | `demo_ecommerce.high_churn_events` | `DIRECT_GUARDED` |
| **Class 1 (In-Place Metadata)** | `C1-06` | **Design Doc §5.1** | History-Based Adaptive Query Optimization | `<YOUR_PROJECT_ID>` | `DIRECT_GUARDED` |
| **Class 1 (In-Place Metadata)** | `C1-08` | **Design Doc §5.1** | Pause Zombie Scheduled Query (0 Readers) | `demo_ecommerce.zombie_export_feed` | `DIRECT_GUARDED` |
| **Class 1 (In-Place Metadata)** | `C1-10` | **Design Doc §5.1** | Unenforced PK/FK Constraints for Join Pruning | `demo_ecommerce.dim_customer` | `DIRECT_GUARDED` |
| **Class 2 (Additive Acceleration)**| `C2-02` | **Design Doc §5.2** | BigQuery Search Indexes with Watchdog Limits | `demo_ecommerce.support_tickets_archive` | `DIRECT_GUARDED` |
| **Class 3 (Structural Rebuild)** | `C3-03` | **Design Doc §5.3** | Partition Granularity Tuning (Day $\to$ Month) | `demo_ecommerce.iot_sensor_telemetry` | `S0_S9_COPY_SWAP` |
| **Class 3 / Workload (FinOps)** | `W-01` / `C1-07` | **Design Doc §5.1** | Editions Fit Sizing (On-Demand $\to$ Enterprise) | `demo_ecommerce` Workload | `ADVISORY_SIZING` |
| **Class 4 (SQL Anti-Patterns)** | `C4-04` | **Design Doc §5.4** | Self-Join $\to$ Analytic Window Function Rewrite | Query Hash `hash_c4_self_join` | `CI_PULL_REQUEST` |
| **Class 4 (SQL Anti-Patterns)** | `C4-07` | **Design Doc §5.4** | Exact `COUNT(DISTINCT)` $\to$ `APPROX_COUNT_DISTINCT` | Query Hash `hash_c4_count_distinct` | `CI_PULL_REQUEST` |

---

## 🚀 3. Live Demo Step-by-Step Execution Runbook

Follow these exact steps during your technical review or customer demonstration:

### 🟢 Step 1: Provision the Demo 2 Synthetic Workload
Run the Demo 2 setup script to provision test tables, simulate streaming ingestion, seed 1,200 micro-partitions, inject high-churn physical storage, and seed realistic query logs into `optimizer_ops`:

```bash
./scripts/demo_setup_2.sh
# or: python3 scripts/demo_setup_2.py
```

* **💡 Plain English (Why This Step Is Important & Why We're Doing It):**
  * **The Problem:** Enterprise features like two-person approvals, storage churn, streaming buffer detection, and partition granularity tuning cannot be tested on trivial hello-world tables.
  * **Why We Do It (Real-World Analogy):** We create a dedicated enterprise test environment with 7 specialized tables: high-churn CDC tables, abandoned nightly ETL pipelines, unconstrained dimension tables, needle-in-haystack support archives, and micro-partitioned IoT feeds. This allows you to demo every advanced capability in seconds.

* **🗣️ What You Say To The Customer (Speaker Notes):**
  > *"We begin by running `demo_setup_2.sh`. This sets up our enterprise demo fixtures: tables with high physical time-travel churn, an abandoned nightly ETL table with zero downstream readers, a 1,200-partition IoT table nearing BigQuery partition limits, and simulated query telemetry demonstrating self-joins and high-cardinality count distinct queries."*

* **🔍 BigQuery Studio Verification SQL (Step 1):**
  ```sql
  SELECT 
    table_id,
    total_rows,
    ROUND(total_logical_bytes / POW(1024, 3), 2) AS logical_gib,
    ROUND(time_travel_physical_bytes / POW(1024, 3), 2) AS time_travel_physical_gib
  FROM `<YOUR_PROJECT_ID>.optimizer_ops.table_state_daily`
  WHERE snapshot_date = CURRENT_DATE()
  ORDER BY time_travel_physical_bytes DESC;
  ```
  * **Expected Output:**
    | table_id | total_rows | logical_gib | time_travel_physical_gib |
    | :--- | :--- | :--- | :--- |
    | `high_churn_events` | `5000000` | `8.00` | `18.00` |
    | `support_tickets_archive` | `8000000` | `12.00` | `0.00` |
    | `product_catalog` | `500000` | `2.00` | `0.00` |
    | `dim_customer` | `100000` | `0.20` | `0.00` |

---

### 🟢 Step 2: Start & Open the Web Review UI
Launch the control plane web review application:

```bash
python3 review_app/main.py
```

Open your browser and navigate to:
👉 **[http://localhost:8080](http://localhost:8080)**

---

### 🟢 Step 3: Present Dynamic Honest Math & Ownership Badges
In the Review UI, inspect the newly emitted cards:

1. **Card: `demo_ecommerce.product_catalog` (Dynamic Workload-Capped Scoring)**
   * **The Native Google Claim:** `~~$8,000.00 / month~~`
   * **Audited 30-Day Table Spend:** `$800.00 / month`
   * **Control Plane Audited Net Value:** **`$544.00 / month`** ($800 spend $\times$ 68% scan reduction cap).
   * **Confidence Score:** `0.35` (Includes $d_{\text{summation}} = 0.50$ penalty for multi-stage query overlap).
   * **Risk Note:** `⚠ NATIVE_ESTIMATE_CAPPED_AT_TABLE_SPEND`

2. **Card: `demo_ecommerce.high_churn_events` (Ownership Attribution)**
   * **Owner Badge:** `👤 finops-core-team@company.com`
   * **Resolution Method:** `RESOLVED_VIA_RESOURCE_LABEL`
   * **Action:** `SET_TIME_TRAVEL_WINDOW (48h)`
   * **Projected Savings:** `$43.20 / month` in physical storage elimination.

* **🗣️ What You Say To The Customer (Speaker Notes):**
  > *"Notice the `product_catalog` card. Google Active Assist promised \$8,000/month in savings. However, our engine audited the table's real 30-day read logs and found the entire table only spent \$800/month! Our dynamic summation cap clamped the gross savings to \$544/mo and applied a 50% confidence penalty. There are no hardcoded table checks—this honest math protects finance credibility across any table in the enterprise."*

---

### 🟢 Step 4: Execute the Two-Person Approval Workflow (§8)
Find the card for **`demo_ecommerce.iot_sensor_telemetry`** (`C3-03` Partition Granularity Rebuild):
Because this is a **Class 3 structural change**, a single click cannot approve it!

1. **Sign-off 1 (Table Owner Approval):**
   * In the card's approval dropdown, select **`Asset Owner (alice-owner@company.com)`**.
   * Click **"Approve (Sign-off 1/2)"**.
   * Notice the card badge updates to: `🟡 PENDING_REVIEW (1/2 Approvals — Awaiting Platform Approver)`.
   * Execution remains locked!

2. **Sign-off 2 (Platform / FinOps Final Approval):**
   * Select **`Platform / FinOps (finops-lead@company.com)`**.
   * Click **"Approve (Sign-off 2/2)"**.
   * The change set transitions to `APPROVED` and moves to the execution queue.

* **🔍 BigQuery Studio Verification SQL (Step 4):**
  ```sql
  SELECT 
    c.change_set_id,
    c.target_table,
    c.state,
    app.principal AS approver,
    app.role AS approval_role,
    FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S UTC', app.`at`) AS approved_at
  FROM `<YOUR_PROJECT_ID>.optimizer_ops.change_sets` c,
  UNNEST(approvals) AS app
  WHERE c.target_table = 'iot_sensor_telemetry';
  ```
  * **Expected Output:**
    | change_set_id | target_table | state | approver | approval_role | approved_at |
    | :--- | :--- | :--- | :--- | :--- | :--- |
    | `e52b19cf-...` | `iot_sensor_telemetry` | `APPROVED` | `alice-owner@company.com` | `owner_approver` | `2026-09-01 20:30:10 UTC` |
    | `e52b19cf-...` | `iot_sensor_telemetry` | `APPROVED` | `finops-lead@company.com` | `platform_approver` | `2026-09-01 20:30:45 UTC` |

---

### 🟢 Step 5: Execute Approved Changes in Terminal
Approve the remaining cards in the UI:
- `high_churn_events` (`C1-04`)
- `zombie_export_feed` (`C1-08`)
- `dim_customer` (`C1-10`)
- `support_tickets_archive` (`C2-02`)

Now run the CLI Executor:

```bash
python3 -m optimizer.cli execute
```

* **🗣️ What You Say To The Customer (Speaker Notes):**
  > *"Watch the executor output in the terminal:  
  > 1. `high_churn_events` executes `ALTER SCHEMA ... SET OPTIONS (max_time_travel_hours = 48)`.  
  > 2. `zombie_export_feed` pauses the unused ETL scheduled query.  
  > 3. `dim_customer` adds an unenforced Primary Key constraint so the BigQuery query planner can eliminate redundant joins.  
  > 4. `support_tickets_archive` creates a BigQuery Search Index and automatically registers a cost watchdog to alert if index storage exceeds 25 GiB.  
  > 5. `iot_sensor_telemetry` runs our zero-loss S0–S9 copy-swap state machine: building a monthly partitioned staging table and validating row checksums before atomic swap."*

* **🔍 BigQuery Studio Verification SQL (Step 5):**

  **5A. Verify Search Index Created with Active Status (`C2-02`):**
  ```sql
  SELECT table_name, index_name, index_status, coverage_percentage
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.INFORMATION_SCHEMA.SEARCH_INDEXES`
  WHERE table_name = 'support_tickets_archive';
  ```
  * **Expected Output:**
    | table_name | index_name | index_status | coverage_percentage |
    | :--- | :--- | :--- | :--- |
    | `support_tickets_archive` | `idx_search_all` | `ACTIVE` | `100.0` |

  **5B. Verify Primary Key Constraint Added (`C1-10`):**
  ```sql
  SELECT table_name, constraint_name, constraint_type, enforced
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.INFORMATION_SCHEMA.TABLE_CONSTRAINTS`
  WHERE table_name = 'dim_customer';
  ```
  * **Expected Output:**
    | table_name | constraint_name | constraint_type | enforced |
    | :--- | :--- | :--- | :--- |
    | `dim_customer` | `pk_dim_customer` | `PRIMARY KEY` | `NO` |

  **5C. Verify Cost Watchdogs Registered in Operations Control Plane:**
  ```sql
  SELECT target_table, watchdog_type, limit_threshold_val, unit, status
  FROM `<YOUR_PROJECT_ID>.optimizer_ops.cost_watchdogs`
  WHERE status = 'ACTIVE';
  ```
  * **Expected Output:**
    | target_table | watchdog_type | limit_threshold_val | unit | status |
    | :--- | :--- | :--- | :--- | :--- |
    | `support_tickets_archive` | `INDEX_STORAGE` | `25.0` | `GIB` | `ACTIVE` |

---

### 🟢 Step 6: Verify Realized Savings & Print Storage CFO Receipts
Run the Verifier:

```bash
python3 -m optimizer.cli verify
```

* **💡 Plain English (Why This Step Is Important & Why We're Doing It):**
  * **The Problem:** Storage optimizations (like reducing time-travel from 7 days to 2 days) don't save query scan bytes—they eliminate physical disk allocation charges. Traditional query scanners miss these savings entirely.
  * **Why We Do It (Real-World Analogy):** The Verifier audits physical and logical storage deltas from `table_state_daily` snapshots, converts freed GiBs into exact dollars ($0.04/GiB for physical, $0.02/GiB for logical), and records an immutable cryptographic receipt.

* **🔍 BigQuery Studio Verification SQL (Step 6):**
  ```sql
  SELECT 
    target_table,
    predicted_usd,
    realized_usd,
    savings_basis,
    state,
    applied_at
  FROM `<YOUR_PROJECT_ID>.optimizer_ops.v_receipts`
  WHERE target_table IN ('high_churn_events', 'support_tickets_archive', 'iot_sensor_telemetry');
  ```
  * **Expected Output:**
    | target_table | predicted_usd | realized_usd | savings_basis | state | applied_at |
    | :--- | :--- | :--- | :--- | :--- | :--- |
    | `high_churn_events` | `43.20` | `43.20` | `STORAGE` | `VERIFIED` | `2026-09-01 20:35:12 UTC` |
    | `iot_sensor_telemetry` | `30.00` | `null` | `SLOT_EDITIONS` | `VERIFIED` | `2026-09-01 20:35:15 UTC` |
    | `support_tickets_archive`| `25.00` | `null` | `BYTES_ON_DEMAND` | `VERIFIED` | `2026-09-01 20:35:18 UTC` |

---

### 🟢 Step 7: Demonstrate Streaming Buffer Drain Cutover (§9.4 S2b)
Show the audience how the control plane handles tables undergoing live streaming ingestion (`demo_ecommerce.streaming_events_active`):

1. **Test A: Default Safety Circuit Breaker (Blocked)**
   * When an engineer attempts to alter a table with rows in the BigQuery streaming buffer (`streaming_buffer_bytes > 0`), the executor immediately halts:
   * **Console Output:** `❌ EXECUTION HALTED: ACTIVE_STREAMING_BUFFER_DETECTED on streaming_events_active (uncommitted rows present). Aborting swap to prevent data loss.`

2. **Test B: Graceful Drain Cutover (`allow_streaming_cutover: true`)**
   * When configured for drain cutover, the executor:
     1. Signals upstream producer pause (or buffer drain window).
     2. Polls BigQuery `TABLE_STORAGE` until `streaming_buffer_bytes == 0`.
     3. Executes the atomic S0–S9 metadata swap in under 200 milliseconds.
     4. Resumes upstream stream ingestion with zero lost rows!

---

### 🟢 Step 8: Generate GitHub PRs for C4 Anti-Patterns
For code-level SQL refactoring (`C4-04` Self-Join and `C4-07` Count Distinct), generate production-ready pull requests:

```bash
python3 scripts/demo_git_pr.py <change_set_id>
```

* **Sample Git Pull Request Output (`C4-04` Self-Join Rewrite):**
  ```diff
  --- a/models/analytics/queries/hash_c4_self_join.sql
  +++ b/models/analytics/queries/hash_c4_self_join.sql
  - SELECT a.event_id, b.event_id 
  - FROM demo_ecommerce.high_churn_events a 
  - JOIN demo_ecommerce.high_churn_events b 
  -   ON a.event_type = b.event_type AND a.event_time < b.event_time
  + -- Refactored self-join into window function (avoids scanning 8 GiB table twice):
  + SELECT 
  +   event_id, 
  +   LEAD(event_id) OVER (PARTITION BY event_type ORDER BY event_time) AS next_event_id
  + FROM demo_ecommerce.high_churn_events
  ```

* **Sample Git Pull Request Output (`C4-07` Approximate Count Distinct):**
  ```diff
  --- a/models/analytics/queries/hash_c4_count_distinct.sql
  +++ b/models/analytics/queries/hash_c4_count_distinct.sql
  - SELECT event_type, COUNT(DISTINCT event_id) AS unique_events
  - FROM demo_ecommerce.high_churn_events GROUP BY 1
  + SELECT event_type, APPROX_COUNT_DISTINCT(event_id) AS unique_events
  + FROM demo_ecommerce.high_churn_events GROUP BY 1
  ```

---

### 🟢 Step 9: Post-Demo Reset & Cleanup
Reset the environment when done:

```bash
./scripts/demo_cleanup.sh
```

---

## 🔬 4. Deep Dive: Technical Evidence, Schema DDL & Mechanics

---

### 1️⃣ Dynamic Workload-Capped Honest Scoring
*(Reference: Design Document §6.2)*

* **Target Table:** `demo_ecommerce.product_catalog`
* **The Problem:** Google Active Assist and external catalog tools evaluate optimization rules on individual queries in isolation. A single recommendation might claim $8,000/month in savings on a table that only incurs $800/month in total read costs. Presenting that $8,000 claim to the CFO destroys engineering credibility.
* **Our Control Plane Solution:**
  1. The scoring engine queries `v_table_read_write_90d` for the actual historical read spend ($S$).
  2. The gross savings claim is capped dynamically:
     $$\text{Capped Savings} = \min(\text{Claimed Savings}, S \times \text{max\_scan\_reduction})$$
  3. For clustering recommendations, $\text{max\_scan\_reduction} = 0.68$.
  4. On `product_catalog` ($S = \$800/\text{mo}$), the $8,000 native claim is mathematically capped to **$544.00/month** ($800 \times 0.68$).
  5. The multi-stage overlap discount $d_{\text{summation}} = 0.50$ is applied, halving confidence to reflect potential subquery overlap.

---

### 2️⃣ Multi-Tiered Ownership Attribution
*(Reference: Design Document §8 & §9.1)*

* **Resolution Hierarchy:**
  ```
  Table Metadata Label [owner: team]
         │ (if missing)
         ▼
  Git Repository / IAC Codeowners
         │ (if missing)
         ▼
  Top Writer (Active 30d ETL Service Account)
         │ (if missing)
         ▼
  Dataset IAM Admin (roles/bigquery.admin)
  ```
* **Why This Matters:** In large data lakehouses with 5,000+ tables, alerts sent to generic mailing lists are ignored. Our engine resolves the exact engineering owner and routes Slack / Jira tickets directly to the responsible team.

---

### 3️⃣ Two-Person Approval Governance
*(Reference: Design Document §8)*

* **State Machine Diagram:**
  ```mermaid
  stateDiagram-v2
      [*] --> PENDING_REVIEW: Rule Triggered
      PENDING_REVIEW --> PENDING_REVIEW: Sign-off 1 (Table Owner Approves)
      note right of PENDING_REVIEW: State remains PENDING_REVIEW\n(Awaiting Platform Approver)
      PENDING_REVIEW --> APPROVED: Sign-off 2 (Platform/FinOps Approves)
      note right of APPROVED: 2 distinct approvers verified\nExecution Unlocked!
      APPROVED --> APPLYING: CLI Executor Runs
      APPLYING --> VERIFYING: S0-S9 Swap Complete
      VERIFYING --> VERIFIED: Verifier Confirms Savings
  ```

---

### 4️⃣ Time-Travel Window Tuning (`C1-04`)
*(Reference: Design Document §5.1)*

* **Target Table:** `demo_ecommerce.high_churn_events`
* **Underlying SQL:**
  ```sql
  ALTER SCHEMA `<YOUR_PROJECT_ID>.demo_ecommerce` SET OPTIONS (max_time_travel_hours = 48);
  ```
* **Savings Formula:**
  $$\Delta\text{Savings} = (\text{Time-Travel Physical GiB} \times 0.60) \times \$0.04/\text{GiB-month}$$
* **Impact:** Reduces physical storage churn from 7 days (168 hours) to 2 days (48 hours), instantly trimming 10.8 GiB of hidden storage charges with zero downtime.

---

### 5️⃣ Unenforced Primary & Foreign Key Constraints (`C1-10`)
*(Reference: Design Document §5.1)*

* **Target Table:** `demo_ecommerce.dim_customer`
* **Underlying SQL:**
  ```sql
  ALTER TABLE `<YOUR_PROJECT_ID>.demo_ecommerce.dim_customer`
  ADD PRIMARY KEY (id) NOT ENFORCED;
  ```
* **Why This Saves Money:**
  BigQuery's SQL optimizer uses unenforced PK/FK metadata to perform **join elimination** and **join reordering**. If a downstream query selects attributes only from `orders` while joining to `dim_customer` on `id`, the BigQuery engine can completely eliminate the join stage, saving 100% of the slot-ms and shuffle costs for that dimension!

---

### 6️⃣ BigQuery Search Indexes with Runaway Cost Watchdogs (`C2-02`)
*(Reference: Design Document §5.2)*

* **Target Table:** `demo_ecommerce.support_tickets_archive`
* **Underlying SQL:**
  ```sql
  CREATE SEARCH INDEX idx_search_all ON `<YOUR_PROJECT_ID>.demo_ecommerce.support_tickets_archive`(ALL COLUMNS);
  ```
* **Watchdog Protection:**
  Search indexes accelerate text and ID lookups by 10x–50x, but BigQuery charges ongoing storage fees for index maintenance. Our control plane automatically provisions an active cost watchdog in `optimizer_ops.cost_watchdogs`:
  * **Watchdog Limit:** `25.0 GiB`
  * **Action on Threshold Breach:** Alerts Slack & FinOps before index maintenance costs exceed query savings.

---

### 7️⃣ Partition Granularity Tuning (`C3-03`)
*(Reference: Design Document §5.3)*

* **Target Table:** `demo_ecommerce.iot_sensor_telemetry`
* **The Problem:** The table contains 1,200 daily partitions, each averaging only 8 MB. BigQuery tables have a hard ceiling of 10,000 partitions. Furthermore, excessive micro-partitions force BigQuery slots to spend more time coordinating metadata than reading data blocks.
* **The Solution:** The engine recompiles the partition specification from `DAY` to `MONTH` granularity, consolidating 1,200 tiny partitions into 40 dense partitions, eliminating query coordination latency and keeping the table far below the 10,000 partition ceiling.

---

## 🏆 5. Demo Summary & Executive Takeaways

| Customer Pain Point | Default Native Recommender | BigQuery Optimization Control Plane |
| :--- | :--- | :--- |
| **Credibility of Numbers** | Inflated promises ($8k claim on an $800 table) | **Workload-Capped Honest Math** with $d_{\text{summation}}$ penalties |
| **Operational Risk** | Unvetted 1-click apply breaks production | **Two-Person Approval Governance** (Owner + FinOps) |
| **Streaming Data Loss** | Drops tables mid-stream causing row loss | **Safe Drain Cutover** with live buffer verification |
| **Storage Spend Waste** | Ignores physical time-travel churn | **Physical Storage Window Tuning** (`C1-04`) with verified receipts |
| **Uncontrolled Index Costs** | Recommends indexes with unbounded fees | **Automated Cost Watchdogs** with threshold alerts |
| **Code Anti-Patterns** | Direct edits to production SQL | **Automated Git PRs** with syntax diffs & blast radius tests |
