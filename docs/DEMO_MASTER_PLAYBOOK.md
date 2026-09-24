# 🎬 BigQuery Optimization Control Plane: Master Customer Demo Playbook
*Comprehensive Step-by-Step Field Guide for Customer Demos & Technical Reviews*

---

## 🌟 1. Executive Storyline (Your Opening 60 Seconds)

> *"In enterprise BigQuery environments (across fortune-500 enterprises), teams spend \$500k–\$1M+ per month across thousands of unmonitored queries and petabytes of storage. Native Google Active Assist and catalog tools like Atlan generate generic recommendations, but data engineers are terrified of clicking 'apply' because an un-vetted partition change can break Looker dashboards, fail ETL jobs, or wipe out IAM security policies.*
> 
> *Furthermore, Active Assist often promises \$50k in savings on queries that only cost \$10k to begin with.*
> 
> *Our **BigQuery Optimization Control Plane** solves this with three enterprise guarantees:*
> 1. **Honest Scoring**: We enforce **Workload-Capped Honest Scoring** so recommendations can never promise more savings than actual historical spend, de-duplicating multi-stage query overlap.
> 2. **Non-Destructive S0–S9 Execution**: Every structural table rebuild uses \$0 zero-copy backup clones, row-level cryptographic checksum validation, and automated IAM/RLS policy preservation.
> 3. **Closed-Loop CFO Receipts**: We freeze performance baselines upon approval, measure post-apply query traces, and print cryptographic proof of realized dollar savings."*

---

## 🗺️ 2. Master Design Document Mapping Matrix

| Optimization Class | Rule Code | Design Doc Section | Real-World Remediation | Live Demo Target Asset | Execution Mode |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Class 1 (In-Place Metadata)** | `C1-01` | **Design Doc §5.1 & §9.2** | Table Clustering on Filter Keys | `demo_ecommerce.orders` | `DIRECT_GUARDED` |
| **Class 1 (In-Place Metadata)** | `C1-02` | **Design Doc §5.1 & §9.2** | Require Partition Filter (Clean Apply) | `demo_ecommerce.customer_events_clean` | `DIRECT_GUARDED` |
| **Class 1 (In-Place Metadata)** | `C1-02` | **Design Doc §5.1 & §9.2** | Pre-Apply Safety Circuit Breaker (Blocked) | `demo_ecommerce.clickstream_events` | `DIRECT_GUARDED` (Halted) |
| **Class 1 (In-Place Metadata)** | `C1-03` | **Design Doc §5.1 & §9.2** | Staging Dataset Default Expiration | `demo_scratch` | `DIRECT_GUARDED` |
| **Class 1 (In-Place Metadata)** | `C1-05` | **Design Doc §5.1 & §9.2** | Storage Billing Model Flip (Physical) | `demo_ecommerce` | `DIRECT_GUARDED` |
| **Class 2 (Additive Acceleration)**| `C2-01` | **Design Doc §5.2 & §9.3** | Materialized Views with Cost Watchdogs | `demo_ecommerce.orders_daily_mv` | `DIRECT_GUARDED` |
| **Class 3 (Workload Billing)** | `W-01` | **Design Doc §5.3 & §8** | Project-Wide Editions Reservation (**Option 1:** `100-Slot Baseline + Autoscale` vs **Option 2:** `0-Baseline Pure Autoscale` with interactive DDL toggle) | `Project-Wide Workload (All Datasets & Tables)` | `DIRECT_GUARDED` (2-Person Approval) |
| **Class 3 (Structural Rebuild)** | `C3-01` | **Design Doc §5.3 & §9.4** | S0–S9 Copy-Swap Table Partitioning | `demo_ecommerce.audit_logs_unpartitioned` | `DIRECT_GUARDED` (S0–S9) |
| **Class 3 (Structural Rebuild)** | `C3-02` | **Design Doc §5.3 & §9.4** | Date-Sharded Table Consolidation | `demo_ecommerce.ga_sessions_*` (30 shards) | `DIRECT_GUARDED` (S0–S9) |
| **Class 3 (Structural Rebuild)** | `C3-05` | **Design Doc §5.3 & §9.4** | Cold Data GCS Parquet Export & Drop | `demo_ecommerce.temp_staging_inactive` | `DIRECT_GUARDED` (GCS Export) |
| **Class 4 (Tri-Engine SQL)** | `C4-01..09` | **Design Doc §5.4 (Engine 1)** | Built-in Python Regex & Heuristic SQL Rewriter (`SELECT *`, `DATE()` wrap, `CROSS JOIN`, `NOT IN`, `ORDER BY`) | `jobs_events` Expensive Queries | `CI_PULL_REQUEST` |
| **Class 4 (Tri-Engine SQL)** | `C4-AST-*` | **Design Doc §5.4 (Engine 2)** | Bundled Google Official ZetaSQL Java AST `.jar` Structural Analyzer (`Dual-Engine Verified`) | `jobs_events` Structural AST Traps | `CI_PULL_REQUEST` |
| **Class 4 (Tri-Engine SQL)** | `C4-AI-*` | **Design Doc §5.4 (Engine 3)** | Vertex AI Gemini 2.5 Flash Schema-Aware SQL Judge + Live BigQuery `dry_run=True` Byte-Reduction Verifier | `jobs_events` Complex Semantic Queries | `CI_PULL_REQUEST` |

---

## 🚀 3. Live Demo Step-by-Step Execution Runbook

Follow these exact steps during your live presentation with the customer:

### 🟢 Step 1: Provision the Synthetic Demo Environment
Run the setup script to create the synthetic workload dataset `demo_ecommerce`, populate sample tables (`orders`, `customer_events_clean`, `clickstream_events`, `audit_logs_unpartitioned`, `temp_staging_inactive`), and seed 90 days of query history into `optimizer_ops`:

```bash
./scripts/demo_setup.sh
# or: python3 scripts/demo_setup.py
```

* **💡 Plain English (Why This Step Is Important & Why We're Doing It):**
  * **The Problem:** In a live customer meeting or test run, you cannot touch the client's actual production database or wait 30 days for live query logs to accumulate.
  * **Why We Do It (Real-World Analogy):** Think of this as setting up a flight simulator before flying a real plane. We generate sample messy tables (`orders` with no clustering, `audit_logs` with no partitioning, 30 scattered daily tables) and inject 90 days of realistic query telemetry. This gives the optimizer realistic data to analyze in seconds without risking real data.

* **🗣️ What You Say To The Customer (Speaker Notes):**
  > *"First, we run `demo_setup.py`. Under the hood, this sets up the `optimizer_ops` control plane schema, provisions sample workload datasets (`orders`, `customer_events_clean`, `clickstream_events`, `audit_logs_unpartitioned`, 30 date shards), and populates 90 days of simulated query telemetry. This simulates what happens when our collector first connects to your GCP environment."*

* **🔍 BigQuery Studio Verification SQL (Step 1):**
  > **1A. Verify Table Read/Write Stats & 90-Day Spend:**
  ```sql
  SELECT 
    project_id, dataset_id, table_id,
    scan_jobs,
    ROUND(est_on_demand_usd_reads, 2) AS monthly_spend_usd,
    ROUND(bytes_billed_reads / POW(1024, 4), 2) AS tib_scanned
  FROM `<YOUR_PROJECT_ID>.optimizer_ops.v_table_read_write_90d`
  ORDER BY est_on_demand_usd_reads DESC;
  ```

  * **Expected Output:**
    | project_id | dataset_id | table_id | scan_jobs | monthly_spend_usd | tib_scanned |
    | :--- | :--- | :--- | :--- | :--- | :--- |
    | `<YOUR_PROJECT_ID>` | `demo_ecommerce` | `orders` | `240` | `1500.00` | `240.00` |
    | `<YOUR_PROJECT_ID>` | `demo_ecommerce` | `audit_logs_unpartitioned` | `90` | `73.24` | `11.72` |
    | `<YOUR_PROJECT_ID>` | `demo_ecommerce` | `customer_events_clean` | `80` | `50.00` | `8.00` |
    | `<YOUR_PROJECT_ID>` | `demo_ecommerce` | `clickstream_events` | `80` | `50.00` | `8.00` |

  > **1B. Verify Hourly Slot Concurrency & Edition Sizing Baseline (`jobs_hourly_slots`):**
  ```sql
  SELECT 
    FORMAT_TIMESTAMP('%Y-%m-%d %H:00 UTC', hour_ts) AS hour_window,
    jobs AS active_queries,
    ROUND(total_slot_ms / (3600 * 1000), 1) AS avg_slots_in_use,
    ROUND(total_slot_ms / 3600000 * 0.06, 2) AS est_enterprise_cost_usd
  FROM `<YOUR_PROJECT_ID>.optimizer_ops.jobs_hourly_slots`
  ORDER BY hour_ts DESC
  LIMIT 5;
  ```
  * **Expected Output:**
    | hour_window | active_queries | avg_slots_in_use | est_enterprise_cost_usd |
    | :--- | :--- | :--- | :--- |
    | `2026-09-16 19:00 UTC` | `83` | `558.7` | `33.52` |
    | `2026-09-16 18:00 UTC` | `101` | `533.7` | `32.02` |
    | `2026-09-16 17:00 UTC` | `119` | `503.3` | `30.20` |
    | `2026-09-16 16:00 UTC` | `84` | `468.3` | `28.10` |
    | `2026-09-16 15:00 UTC` | `116` | `564.8` | `33.89` |

  * **🗣️ Enterprise Edition Sizing & Commitment Talking Points (Executive FinOps Talking Points):**
    - **Steady Off-Peak Floor (`~271–303 slots`)**: Even overnight and on weekends, continuous ETL/CDC ingestion maintains a steady floor around **271.2 slots** (P25 = **303.3 slots**).
    - **Recommended Baseline Commitment (`300 Slots`)**: Commit **300 Enterprise Slots** on a 1-Year or 3-Year commitment (`$0.048/slot-hour` 1-Yr or `$0.036/slot-hour` 3-Yr vs `$0.06` pay-as-you-go), locking in a 20%–40% discount on your 24x7 base load with zero wasted idle slots.
    - **Peak Business Bursts (`468–629 slots`)**: During daytime BI & dashboarding hours (`13:00–21:00 UTC`), demand spikes up to **629.1 slots**.
    - **Recommended Autoscaling Ceiling (`Max Reservation = 650 Slots`)**: Configure **300 Baseline + 350 Autoscaling Slots** (`Max = 650`). BigQuery automatically scales up in increments of 50 slots during daytime surges and scales right back down to 300 overnight—eliminating slot queuing without paying for peak capacity 24/7.


---

### 🟢 Step 2: Start & Open the Web Review UI
Start the web app in the background (or run in a separate terminal tab):

```bash
python3 review_app/main.py
```

Open Chrome and navigate to:
👉 **[http://localhost:8080](http://localhost:8080)**

* **💡 Plain English (Why This Step Is Important & Why We're Doing It):**
  * **The Problem:** Database optimization tools often dump thousands of lines of raw SQL code or unreadable terminal output, making it impossible for business stakeholders, lead architects, or managers to see what is happening.
  * **Why We Do It (Real-World Analogy):** Think of this as the dashboard on an airplane cockpit. It gives engineering managers, architects, and FinOps leads a clean visual web portal to see every optimization opportunity, reviewed and stack-ranked, with complete human-in-the-loop governance before anything touches BigQuery.

* **🗣️ What You Say To The Customer (Speaker Notes):**
  > *"Next, we launch our Web Review Application. In production, this UI is protected by Google IAP and SSO, giving engineering managers and lead architects a single pane of glass for human-in-the-loop governance before any change touches BigQuery."*

---

### 🟢 Step 3: Present the Review Queue to Stakeholders
1. **Show the Stack-Ranked Queue:** Explain how cards are sorted by **Net Monthly Value ($)** multiplied by **Confidence Score (0.00–1.00)**.
2. **Explain the Honest Scoring Formula:**
   > *"Notice how the top card on `orders` claims \$1,020/month. Google Active Assist originally promised \$10,000/month because native tools evaluate recommendations in isolation. Our engine audited actual 30-day read spend (\$1,500/mo) and applied **Workload-Capped Honest Scoring** (with a $d_{\text{summation}} = 0.50$ multi-stage discount), capping savings to the real 68% scan reduction (\$1,500 spend - \$480 projected = \$1,020/mo savings) to ensure the numbers presented to leadership are 100% credible."*
3. **Walk Through the Visual Evidence & DDL:**
   * **🔴 Current State (Today):** Point to the current scan size (1.0 TiB/query), execution frequency (240 queries/mo), and historical read spend ($1,500/mo).
   * **🟢 Proposed State (Optimized):** Point to the target spec (`CLUSTER BY customer_id, order_status`), expected scan reduction (68%), projected spend ($480/mo), and estimated net savings ($1,020/mo).
   * **⚡ Underlying BigQuery DDL:** Point to the formatted execution SQL (`ALTER TABLE ... SET CLUSTER BY ...`) and blast radius risk warning (`⚠ RECLUSTER_OF_EXISTING_DATA_NOT_AUTOMATIC`).
   * **🔍 Raw Telemetry JSON:** (Optional) Expand the bottom drawer for deep raw metadata inspection.

* **💡 Plain English (Why This Step Is Important & Why We're Doing It):**
  * **The Problem:** Default cloud recommendations (like Google Active Assist) evaluate recommendations in isolation without checking whether they overlap or exceed actual spend, often promising impossible numbers (e.g., claiming $10,000 in monthly savings on a table that only spends $1,500/month) due to multi-stage subquery overlap. This makes finance teams distrust data engineering.
  * **Why We Do It (Real-World Analogy):** This is our "Honest Math" demonstration. We show the customer that our engine audits actual monthly spend, caps savings so they never exceed reality (Workload-Capped Honest Scoring), and displays an easy-to-read Red box (Current Wasteful State), Green box (Optimized State), and Dark code block (Exact BigQuery SQL). No surprises, no fabricated numbers.

* **🗣️ What You Say To The Customer (Speaker Notes):**
  > *"Every card gives your team complete visibility. The Red box shows your current wasteful baseline, the Green box shows the exact projected savings, and the Dark code block shows the exact BigQuery DDL that will be executed."*

* **🔍 BigQuery Studio Verification SQL (Step 3):**
  > Run this SQL in BigQuery Studio to inspect the queue view read by the UI:
  ```sql
  SELECT 
    change_set_id,
    apply_class,
    target_dataset,
    target_table,
    net_monthly_value_usd,
    confidence,
    score,
    state
  FROM `<YOUR_PROJECT_ID>.optimizer_ops.v_pending_review`
  ORDER BY net_monthly_value_usd DESC;
  ```

  * **Expected Output:**
    | change_set_id | apply_class | target_dataset | target_table | net_monthly_value_usd | confidence | score | state |
    | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
    | `b75a56a6-e58b-4a6e-868b-6e4e1615529d` | `1` | `demo_ecommerce` | `orders` | `1020.00` | `0.30` | `306.00` | `PENDING_REVIEW` |

    > *Dollar figures are computed live from the seeded telemetry (spend x 0.68 scan-reduction cap for orders, spend x 0.85 for audit logs), so exact values can drift slightly if seed timing shifts — that's the honest math working, not a bug.*
    | `8730321c-466e-4983-ae52-5feca7b7975c` | `3` | `demo_ecommerce` | `audit_logs_unpartitioned` | `62.05` | `0.49` | `30.40` | `PENDING_REVIEW` |

---

### 🟢 Step 4: Click "Approve" (Human-in-the-Loop Governance)
In the browser UI:
1. Find the card for **`demo_ecommerce.orders`** (Class 1 Table Clustering) and click **"Approve change"**.
2. Find the card for **`demo_ecommerce.audit_logs_unpartitioned`** (Class 3 Repartitioning) and click **"Approve change"**.

* **💡 Plain English (Why This Step Is Important & Why We're Doing It):**
  * **The Problem:** Data engineers are terrified of automated tools making breaking changes without warning. If an un-vetted change breaks a Looker dashboard or nightly ETL pipeline, someone has to take the blame.
  * **Why We Do It (Real-World Analogy):** Think of this like signing a building permit before construction starts. Clicking "Approve" records your email address, takes a snapshot of how the table currently performs (so we can prove dollar savings later), and tells Google Cloud's console "we are handling this".

* **🗣️ What You Say To The Customer (Speaker Notes):**
  > *"When I click 'Approve', three things happen immediately:  
  > 1. The change set state transitions to `APPROVED` in BigQuery.  
  > 2. The 28-day baseline performance snapshot (latency, bytes, slot-ms) is frozen in `verification_plan_json`.  
  > 3. The engine calls Google Active Assist Recommender API to mark the recommendation as `CLAIMED` so it disappears from your GCP Console."*

* **🔍 BigQuery Studio Verification SQL (Step 4):**
  > Run this SQL in BigQuery Studio to verify state updated to `APPROVED`, approver email, Active Assist claim note, and frozen 28-day baseline:
  ```sql
  SELECT 
    c.change_set_id,
    c.target_table,
    c.state,
    sh.actor AS approved_by,
    sh.`at` AS approved_timestamp,
    sh.note AS active_assist_api_claim_note
  FROM `<YOUR_PROJECT_ID>.optimizer_ops.change_sets` c,
  UNNEST(state_history) AS sh
  WHERE c.state = 'APPROVED'
    AND sh.state = 'APPROVED';
  ```

  * **Expected Output:**
    | change_set_id | target_table | state | approved_by | approved_timestamp | active_assist_api_claim_note |
    | :--- | :--- | :--- | :--- | :--- | :--- |
    | `b75a56a6-e58b-4a6e-868b-6e4e1615529d` | `orders` | `APPROVED` | `finops-lead@company.com` | `2026-08-19 16:45:00 UTC` | `CLAIMED:projects/<YOUR_PROJECT_ID>/...` |
    | `8730321c-466e-4983-ae52-5feca7b7975c` | `audit_logs_unpartitioned` | `APPROVED` | `finops-lead@company.com` | `2026-08-19 16:45:05 UTC` | `CLAIMED:projects/<YOUR_PROJECT_ID>/...` |

---

### 🟢 Step 5: Execute Approved Changes in Terminal
Switch to your terminal and run the Executor:

```bash
python3 -m optimizer.cli execute
```

* **💡 Plain English (Why This Step Is Important & Why We're Doing It):**
  * **The Problem:** Traditional optimization scripts drop tables (`DROP TABLE`) and recreate them, causing `404 Table Not Found` downtime, dropped security permissions, and broken dashboards.
  * **Why We Do It (Real-World Analogy):** Think of this like upgrading an airplane engine mid-flight without passengers feeling a bump. For simple metadata changes (Class 1), it takes 0 milliseconds. For deep table restructuring (Class 3), it creates a $0 safety backup clone, builds the new partitioned table in a staging room, checks that all rows match cryptographically, and swaps it in under 200ms with zero downtime and zero lost permissions.

* **🗣️ What You Say To The Customer (Speaker Notes):**
  > *"Now we run `optimizer.cli execute`.  
  > For Class 1 (`orders`), it executes `ALTER TABLE SET CLUSTER BY` in 0ms with zero downtime.  
  > For Class 3 (`audit_logs`), it executes our S0–S9 state machine: it creates a \$0 zero-copy backup clone, builds the new partitioned table in staging, verifies cryptographic row-level checksums, and executes an atomic metadata swap in under 200ms with zero downtime or permission loss."*

* **🔍 BigQuery Studio Verification SQL (Step 5):**
  > Run these SQL queries in BigQuery Studio to verify state transition and DDL options applied to live tables:

  **Query 1: Verify Change Set Lifecycle State (`APPLIED` / `VERIFYING`)**
  ```sql
  SELECT change_set_id, target_table, state, applied_at
  FROM `<YOUR_PROJECT_ID>.optimizer_ops.change_sets`
  WHERE state IN ('APPLIED', 'VERIFYING');
  ```
  * **Expected Output:**
    | change_set_id | target_table | state | applied_at |
    | :--- | :--- | :--- | :--- |
    | `b75a56a6-e58b-4a6e-868b-6e4e1615529d` | `orders` | `VERIFYING` | `2026-08-19 16:49:00 UTC` |
    | `8730321c-466e-4983-ae52-5feca7b7975c` | `audit_logs_unpartitioned` | `VERIFYING` | `2026-08-19 16:54:02 UTC` |

  > *Note: Immediately after applying the DDL, the executor sets `applied_at` and transitions `state` to `VERIFYING` so the post-apply verification window begins collecting query metrics before `optimizer.cli verify` runs in Step 6.*

  **Query 2: Verify Clustering Columns on `orders` (Class 1)**
  ```sql
  SELECT column_name, data_type, clustering_ordinal_position
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.INFORMATION_SCHEMA.COLUMNS`
  WHERE table_name = 'orders'
    AND clustering_ordinal_position IS NOT NULL
  ORDER BY clustering_ordinal_position;
  ```
  * **Expected Output:**
    | column_name | data_type | clustering_ordinal_position |
    | :--- | :--- | :--- |
    | `customer_id` | `STRING` | `1` |
    | `order_status` | `STRING` | `2` |

  **Query 3: Verify Partitioning Column on `audit_logs_unpartitioned` (Class 3)**
  ```sql
  SELECT column_name, data_type, is_partitioning_column
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.INFORMATION_SCHEMA.COLUMNS`
  WHERE table_name = 'audit_logs_unpartitioned'
    AND is_partitioning_column = 'YES';
  ```
  * **Expected Output:**
    | column_name | data_type | is_partitioning_column |
    | :--- | :--- | :--- |
    | `log_timestamp` | `TIMESTAMP` | `YES` |

  **Query 4: Inspect Active Date Partitions on `audit_logs_unpartitioned`**
  ```sql
  SELECT table_name, partition_id, total_rows, ROUND(total_logical_bytes / POW(1024, 2), 2) AS partition_mb
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.INFORMATION_SCHEMA.PARTITIONS`
  WHERE table_name = 'audit_logs_unpartitioned'
  ORDER BY partition_id DESC;
  ```
  * **Expected Output:**
    | table_name | partition_id | total_rows | partition_mb |
    | :--- | :--- | :--- | :--- |
    | `audit_logs_unpartitioned` | `20260819` | `22` | `0.00` |
    | `audit_logs_unpartitioned` | `20260818` | `23` | `0.00` |
    | `audit_logs_unpartitioned` | `20260817` | `23` | `0.00` |
    | `audit_logs_unpartitioned` | `20260816` | `23` | `0.00` |

  **Query 5: Inspect Complete Lifecycle State Audit Trail (`state_history` REPEATED RECORD)**
  ```sql
  SELECT 
    cs.change_set_id,
    cs.target_dataset,
    cs.target_table,
    hist.state AS transition_state,
    FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S UTC', hist.at) AS transitioned_at,
    hist.actor,
    COALESCE(hist.note, '—') AS note
  FROM `<YOUR_PROJECT_ID>.optimizer_ops.change_sets` cs,
  UNNEST(cs.state_history) AS hist
  ORDER BY cs.target_table, hist.at ASC;
  ```
  * **Expected Output:**
    | change_set_id | target_dataset | target_table | transition_state | transitioned_at | actor | note |
    | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
    | `fdc88ec9-...` | `demo_ecommerce` | `audit_logs_unpartitioned` | `PENDING_REVIEW` | `2026-08-30 23:19:11 UTC` | `rules-engine` | — |
    | `fdc88ec9-...` | `demo_ecommerce` | `audit_logs_unpartitioned` | `APPROVED` | `2026-08-30 23:23:43 UTC` | `finops-lead@company.com` | `Approved via UI` |
    | `fdc88ec9-...` | `demo_ecommerce` | `audit_logs_unpartitioned` | `APPLYING` | `2026-08-30 23:36:52 UTC` | `executor` | — |
    | `fdc88ec9-...` | `demo_ecommerce` | `audit_logs_unpartitioned` | `APPLIED` | `2026-08-30 23:38:02 UTC` | `executor` | — |
    | `fdc88ec9-...` | `demo_ecommerce` | `audit_logs_unpartitioned` | `VERIFYING` | `2026-08-30 23:38:06 UTC` | `executor` | — |

---

### 🟢 Step 6: Verify Realized Savings & Print the CFO Receipt
Run the Verifier to measure post-apply query traces against the frozen baseline:

```bash
python3 -m optimizer.cli verify
```

* **💡 Plain English (Why This Step Is Important & Why We're Doing It):**
  * **The Problem:** Most FinOps tools end with someone claiming "trust me, we saved money," but finance directors (CFOs) demand audited, measurable proof.
  * **Why We Do It (Real-World Analogy):** Think of this as the itemized grocery receipt for the CFO. The verifier compares post-change query costs against the frozen pre-change baseline. If queries are faster and cheaper, it writes an immutable savings receipt to BigQuery. If a change caused queries to scan more data, it immediately alerts the team before bills spike.

* **🗣️ What You Say To The Customer (Speaker Notes):**
  > *"Now we run `optimizer.cli verify`. The verifier measures post-apply query traces against the frozen baseline in `verification_plan_json`. It prints an official CFO Proof Receipt in `optimizer_ops.v_receipts` proving actual dollars saved vs predicted savings."*

* **🔍 BigQuery Studio Verification SQL (Step 6):**
  > Run this SQL in BigQuery Studio to inspect the official CFO Proof Receipts ledger:
  ```sql
  SELECT 
    target_dataset,
    target_table,
    predicted_usd,
    realized_usd,
    realized_over_predicted,
    state,
    applied_at
  FROM `<YOUR_PROJECT_ID>.optimizer_ops.v_receipts`
  ORDER BY applied_at DESC;
  ```

  * **Expected Output (After Running `optimizer.cli verify`):**
    | target_dataset | target_table | predicted_usd | realized_usd | realized_over_predicted | state | applied_at |
    | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
    | `demo_ecommerce` | `audit_logs_unpartitioned` | `43.95` | `null` | `null` | `VERIFIED` | `2026-08-19 16:53:54 UTC` |
    | `demo_ecommerce` | `orders` | `1020.00` | `null` | `null` | `VERIFIED` | `2026-08-19 16:47:28 UTC` |

  > *Note — why `realized_usd` is `null` in the demo:* with `verify_window_days: 0`, verification runs seconds after apply, so there is **no post-window telemetry yet**. The verifier is deliberately honest: `VERIFIED` here means "no regressions detected"; the realized dollar figure stays `null` until real post-apply queries accumulate (production uses a 14-day window). The tool never copies the prediction into the realized column — a receipt leadership can trust is the whole point. Talk track: *"Predicted is our audited estimate; Realized fills in from live telemetry over the next two weeks — the two are never allowed to be the same number by construction."*

---

### 🟢 Step 7: Demonstrate a 1-Click Rollback
Show the team what happens if an engineer requests an immediate rollback:

```bash
python3 -m optimizer.cli rollback --id <change_set_id> --reason "Manual 1-Click Rollback requested by operator"
```

* **💡 Plain English (Why This Step Is Important & Why We're Doing It):**
  * **The Problem:** What if a downstream team claims a query broke after an optimization? Without a fast rollback, engineers panic and spend hours restoring backups.
  * **Why We Do It (Real-World Analogy):** Think of this as the "Undo Button with an Insurance Policy". Running rollback swaps the untouched zero-copy backup clone back into production in under 2 seconds, moves the altered table into quarantine for post-mortem inspection, and restores all original security policies instantly.

* **🗣️ What You Say To The Customer (Speaker Notes):**
  > *"If an engineer ever reports an unexpected issue, running `optimizer.cli rollback` swaps the zero-copy clone back into production in under 2 seconds. The broken table is renamed to `_regressed` for post-mortem analysis, and all original IAM policies are restored."*

* **🔍 BigQuery Studio Verification SQL (Step 7):**
  > Run this SQL in BigQuery Studio to verify rollback state transition and audit note:
  ```sql
  SELECT 
    c.change_set_id,
    c.target_table,
    c.state,
    sh.actor AS rolled_back_by,
    sh.`at` AS rolled_back_at,
    sh.note AS rollback_reason
  FROM `<YOUR_PROJECT_ID>.optimizer_ops.change_sets` c,
  UNNEST(state_history) AS sh
  WHERE c.state = 'ROLLED_BACK'
    AND sh.state = 'ROLLED_BACK';
  ```

  * **Expected Output:**
    | change_set_id | target_table | state | rolled_back_by | rolled_back_at | rollback_reason |
    | :--- | :--- | :--- | :--- | :--- | :--- |
    | `b75a56a6-e58b-4a6e-868b-6e4e1615529d` | `orders` | `ROLLED_BACK` | `finops-lead@company.com` | `2026-08-19 17:47:18 UTC` | `Manual 1-Click Rollback requested by operator` |

---

### 🟢 Step 8: Post-Demo Reset & Cleanup
When the demo is complete, reset the environment:

```bash
# Standard Reset (clears demo tables & queue, keeps optimizer_ops ready for next demo):
./scripts/demo_cleanup.sh

# 100% Full Teardown (wipes everything including optimizer_ops):
./scripts/demo_cleanup.sh --wipe-ops
```

* **💡 Plain English (Why This Step Is Important & Why We're Doing It):**
  * **The Problem:** Leaving temporary test tables and dirty state in GCP clutters datasets, incurs unnecessary storage costs, and prevents running the demo fresh for the next customer.
  * **Why We Do It (Real-World Analogy):** Think of this as wiping the chalkboard clean after class. It removes temporary tables, resets the review queue, and leaves the control plane ready for your next presentation or dry run.

* **🗣️ What You Say To The Customer (Speaker Notes):**
  > *"Finally, running `demo_cleanup.sh` resets the demo fixtures back to baseline, leaving `optimizer_ops` clean and ready for the next customer presentation."*

---

## 🔬 4. Deep Dive: Optimization Evidence, Schema DDL & Mechanics

---

### 1️⃣ Class 1: In-Place Metadata Optimizations (Zero Downtime / Zero Rebuild)
*(Reference: Design Document §5.1 & §9.2)*

#### 🎯 Demo Case A: Table Clustering (`C1-01`)
* **Target Table:** `demo_ecommerce.orders`
* **💡 Plain English Explanation (What We Are Doing & Why):**
  > **What We Are Doing:** Reorganizing how rows are physically grouped inside the table by `customer_id` and `order_status` without changing your data, rebuilding the table, or causing any downtime.  
  > **Why We Are Doing It:** Think of it like sorting books in a library by genre and author instead of throwing them in a random heap. When queries search for orders by a specific customer or order status, BigQuery can skip 68% of the unneeded data blocks, saving **$1,020/month** in query scan fees.

* **Card Display in UI:**
  * **Net Value:** `~$1,020 / month` | **Confidence:** `0.30` | **Route:** `DIRECT_GUARDED`
  * **Badges:** `CLASS 1` · `C1-01` · `DIRECT_GUARDED`
  * **Summary:** *"Clustering demo_ecommerce.orders by customer_id, order_status will reduce scanned bytes by up to 68% with zero table downtime."*
  * **Risk Warning:** `⚠ RECLUSTER_OF_EXISTING_DATA_NOT_AUTOMATIC · NATIVE_ESTIMATE_CAPPED_AT_TABLE_SPEND`
* **Google Active Assist Recommendation Callout in UI:**
  * **Google Initial Claim:** `~~$10,000.00 / month (1.6 PB scan reduction)~~`
  * **Control Plane Audited Cap:** **`$1,500.00 / month (100% Table Spend Ceiling)`**
  * **Audit Note:** *"Google Active Assist evaluates recommendations in isolation and can over-count multi-stage query workflows. Our control plane capped gross savings to actual 30-day table spend and applied a 50% confidence penalty (d_summation = 0.50) to ensure CFO-grade accuracy."*
* **Evidence Comparison (Current State vs. Proposed State):**
  | Metric / Dimension | 🔴 Current State (Today) | 🟢 Proposed State (Optimized) |
  | :--- | :--- | :--- |
  | **Clustering** | `None (Unclustered table)` | `CLUSTER BY customer_id, order_status` |
  | **Avg Query Scan** | `1.0 TiB / query` | `68% scan reduction (~320 GiB / query)` |
  | **Monthly Query Count** | `240 queries / month` | `240 queries / month` |
  | **Monthly Spend** | `$1,500.00 / month` | `$480.00 / month` |
  | **Estimated Net Savings** | `$0.00` | **`$1,020.00 / month ($1,500 current - $480 projected)`** |
* **Underlying BigQuery DDL / Execution SQL:**
  ```sql
  ALTER TABLE `<YOUR_PROJECT_ID>.demo_ecommerce.orders` SET CLUSTER BY customer_id, order_status;
  ```
* **🔍 BigQuery Studio Verification SQL:**
  ```sql
  SELECT column_name, data_type, clustering_ordinal_position
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.INFORMATION_SCHEMA.COLUMNS`
  WHERE table_name = 'orders' AND clustering_ordinal_position IS NOT NULL
  ORDER BY clustering_ordinal_position;
  ```
  * **Expected Output:**
    | column_name | data_type | clustering_ordinal_position |
    | :--- | :--- | :--- |
    | `customer_id` | `STRING` | `1` |
    | `order_status` | `STRING` | `2` |

* **What you say to the customer:**
  > *"Look at this top card on `orders`. Google Active Assist originally promised \$10,000/month. But when our control plane audited the real 30-day query telemetry, the entire table only spent \$1,500/month! Our engine immediately applied **Workload-Capped Honest Scoring**: it capped the savings to actual spend (\$1,500/mo), applied 68% scan pruning to project \$480/mo spend, yielding **\$1,020/month in estimated net savings**, and applied `d_summation = 0.50` (a 50% confidence haircut for multi-stage overlap). We protect your engineering team from presenting inflated numbers to the CFO."*

---

#### 🎯 Demo Case B: Require Partition Filter Guardrail (The 1-2 Punch) (`C1-02`)

* **Part 1: The Safety Circuit Breaker (Blocked Table)**
  * **Target Table:** `demo_ecommerce.clickstream_events`
  * **💡 Plain English Explanation:**
    > **What Happens:** The engine detects 20 queries lacking a filter on `event_date`. When approved and executed, the executor **halts and safely blocks execution** to prevent crashing live production ETL jobs.
  * **Card Display in UI:**
    * **Net Value:** `Guardrail / Risk Prevention` | **Confidence:** `0.95` | **Route:** `DIRECT_GUARDED`
    * **Risk Note:** `⚠ BREAKS_UNFILTERED_QUERIES · PRE_APPLY_CIRCUIT_BREAKER_ACTIVE`
  * **Execution Result:**
    ```
    blocked (demo_ecommerce.clickstream_events): 20 queries in the last 30 days appear to lack a filter on event_date; resolve them or approve with force=true
    ```

* **Part 2: The Live Enforced Guardrail (Clean Apply Table)**
  * **Target Table:** `demo_ecommerce.customer_events_clean`
  * **💡 Plain English Explanation:**
    > **What Happens:** 100% of queries against this table already filter on `event_date`. The executor applies `require_partition_filter = TRUE` in 0ms!
  * **Card Display in UI:**
    * **Net Value:** `Guardrail / Risk Prevention` | **Confidence:** `0.95` | **Route:** `DIRECT_GUARDED`
    * **Summary:** *"100% of production queries against `customer_events_clean` filter on `event_date`. Enforce `require_partition_filter = TRUE` to prevent rogue accidental full-table scans."*
  * **Evidence Comparison (Current vs. Proposed):**
    | Metric / Dimension | Current State (What it is today) | Proposed State (What we are changing it to) |
    | :--- | :--- | :--- |
    | **Option Setting** | `require_partition_filter = FALSE` | `require_partition_filter = TRUE` |
    | **Filtered Queries (ETL)** | `100% of production jobs include date filter` | `100% compliance verified (zero pipeline breakage risk)` |
    | **Accidental Scan Blast Radius** | `Up to $5,000 per rogue query on historical data` | **`$0.00 (Unfiltered queries fail fast in 0ms before billing)`** |
  * **Underlying SQL / Schema DDL (Applied by Executor via UI):**
    ```sql
    ALTER TABLE `<YOUR_PROJECT_ID>.demo_ecommerce.customer_events_clean`
    SET OPTIONS (require_partition_filter = TRUE);
    ```
  * **🔍 BigQuery Studio Live Guardrail Verification:**
    > **Test 1: Rogue Unfiltered Query (Live Error)**
    ```sql
    SELECT * FROM `<YOUR_PROJECT_ID>.demo_ecommerce.customer_events_clean`;
    ```
    * **Live Result:** **💥 FAILS FAST in 0ms!** BigQuery rejects the query before billing:
      `Cannot query over table without a filter over column(s) 'event_date' that can be used for partition elimination`

    > **Test 2: Compliant Filtered Query (Success)**
    ```sql
    SELECT * FROM `<YOUR_PROJECT_ID>.demo_ecommerce.customer_events_clean`
    WHERE event_date = CURRENT_DATE();
    ```
    * **Live Result:** **✅ SUCCEEDS instantly** with 0 wasted scan bytes!

---

#### 🎯 Demo Case C: Storage Billing Model Flip (`C1-05`)
* **Target Dataset:** `demo_ecommerce`
* **💡 Plain English Explanation (What We Are Doing & Why):**
  > **What We Are Doing:** Changing how Google bills for storage in this dataset—switching from **LOGICAL** (uncompressed data size) to **PHYSICAL** (compressed data size).  
  > **Why We Are Doing It:** Think of it like paying for luggage storage based on vacuum-sealed bag size rather than uncompressed size. BigQuery compresses data at an 8:1 ratio. Even though physical storage costs slightly more per GB ($0.04 vs $0.02), paying for 8x fewer bytes slashes your monthly storage bill by 70% (**$280/month saved**).
* **Card Display in UI:**
  * **Net Value:** `~$280 / month` | **Confidence:** `0.85` | **Route:** `DIRECT_GUARDED`
  * **Summary:** *"Dataset `demo_ecommerce` compresses at an 8:1 ratio. Flipping the storage billing model to PHYSICAL saves \$280/mo net of 7-day time-travel and fail-safe bytes."*
* **Evidence Comparison (Current vs. Proposed):**
  | Metric / Dimension | Current State (What it is today) | Proposed State (What we are changing it to) |
  | :--- | :--- | :--- |
  | **Billing Model** | `LOGICAL Storage Billing ($0.02/GB)` | `PHYSICAL Storage Billing ($0.04/GB)` |
  | **Total Billable Storage** | `200 GB Logical Active Storage` | `25 GB Compressed Physical Storage` |
  | **Gross Monthly Storage Cost** | `$400.00 / month` | `$100.00 (base) + $20 (time-travel) = $120.00 / month` |
  | **Estimated Net Savings** | `$0.00` | **`$280.00 / month (70% storage cost reduction)`** |
* **Underlying SQL / API Call:**
  ```sql
  ALTER SCHEMA `<YOUR_PROJECT_ID>.demo_ecommerce`
  SET OPTIONS (storage_billing_model = 'PHYSICAL');
  ```
* **🔍 BigQuery Studio Verification SQL:**
  ```sql
  SELECT schema_name, option_name, option_value
  FROM `<YOUR_PROJECT_ID>.INFORMATION_SCHEMA.SCHEMATA_OPTIONS`
  WHERE schema_name = 'demo_ecommerce' AND option_name = 'storage_billing_model';
  ```
  * **Expected Output:**
    | schema_name | option_name | option_value |
    | :--- | :--- | :--- |
    | `demo_ecommerce` | `storage_billing_model` | `PHYSICAL` |

---

### 2️⃣ Class 2: Additive Performance Objects with Cost Watchdogs
*(Reference: Design Document §5.2 & §9.3)*

#### 🎯 Demo Case: Materialized View with Spend Watchdog (`C2-01`)
* **Target Query Pattern:** Repeated daily aggregation on `demo_ecommerce.orders`
* **💡 Plain English Explanation (What We Are Doing & Why):**
  > **What We Are Doing:** Creating a pre-computed summary table (Materialized View) that automatically updates as new orders arrive, protected by an automated cost watchdog budget ($50/mo).  
  > **Why We Are Doing It:** Think of it like keeping a running bar tab total written on a chalkboard instead of re-adding every individual drink receipt from scratch every time someone asks for the tab. Queries scan < 200 MB instead of 250 GB (99.9% cost reduction), and the cost watchdog ensures auto-refreshes never generate surprise bills.

* **Evidence Comparison (Current vs. Proposed):**
  | Metric / Dimension | Current State (What it is today) | Proposed State (What we are changing it to) |
  | :--- | :--- | :--- |
  | **Query Execution Mode** | `Scans raw orders table (250 GB) 120 times/day` | `Smart rewrite to Materialized View (< 200 MB scan)` |
  | **Daily Query Scan Bytes** | `30,000 GB / day` | `24 GB / day (99.9% query scan reduction)` |
  | **Monthly Query Cost** | `$1,875.00 / month` | `$1.50 / month` |
  | **Automated Cost Watchdog** | `None (Risk of unbounded refresh spend)` | **`Watchdog Active: Capped at $50/mo refresh budget`** |
* **Underlying SQL / Schema DDL:**
  ```sql
  CREATE MATERIALIZED VIEW IF NOT EXISTS `<YOUR_PROJECT_ID>.demo_ecommerce.orders_daily_mv`
  PARTITION BY order_date
  CLUSTER BY customer_id
  AS SELECT 
    order_date, 
    customer_id, 
    order_status, 
    COUNT(*) AS total_orders, 
    SUM(order_amount) AS total_revenue
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.orders`
  GROUP BY 1, 2, 3;
  ```
* **🔍 BigQuery Studio Verification SQL:**
  ```sql
  SELECT table_name, table_type
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.INFORMATION_SCHEMA.TABLES`
  WHERE table_name = 'orders_daily_mv';
  ```
  * **Expected Output:**
    | table_name | table_type |
    | :--- | :--- |
    | `orders_daily_mv` | `MATERIALIZED VIEW` |

---

### 3️⃣ Class 3: Structural Table Rebuilds (S0–S9 Copy-Swap-Rebind)
*(Reference: Design Document §5.3 & §9.4)*

#### 🎯 Demo Case A: Repartitioning Legacy Audit Logs (`C3-01`)
* **Target Table:** `demo_ecommerce.audit_logs_unpartitioned`
* **💡 Plain English Explanation (What We Are Doing & Why):**
  > **What We Are Doing:** Safely rebuilding an unpartitioned table into daily date partitions using our zero-downtime S0–S9 state machine (creating a $0 zero-copy backup clone, row-level cryptographic checksum validation, and atomic metadata swap).  
  > **Why We Are Doing It:** Think of it like taking a giant box of loose, unsorted paper receipts and filing them into daily labeled folders. Instead of searching through the entire 400 GiB box for today's logs, queries jump straight to today's partition, cutting query spend by 85% (**$62.26/month saved**).

* **Card Display in UI:**
  * **Net Value:** `~$62 / month` | **Confidence:** `0.49` | **Route:** `DIRECT_GUARDED`
  * **Summary:** *"Table audit_logs_unpartitioned is unpartitioned (400 GiB, 90 scans/90d). Rebuilding with `PARTITION BY DATE(log_timestamp)` reduces scan waste by 85%."*
* **Evidence Comparison (Current vs. Proposed):**
  | Metric / Dimension | Current State (What it is today) | Proposed State (What we are changing it to) |
  | :--- | :--- | :--- |
  | **Partitioning Spec** | `None (Unpartitioned Table)` | `PARTITION BY DATE(log_timestamp)` |
  | **Table Size / Scans** | `400 GiB (90 full-table scans / 90d)` | `85% daily scan pruning (~60 GiB / query)` |
  | **Monthly Spend on Table**| `$73.24 / month` | `$10.99 / month (15% remaining)` |
  | **Net Realized Savings** | `$0.00` | **`$62.26 / month ($73.24 current - $10.99 projected)`** |
  | **Zero-Copy Backup Clone** | `None` | **`$0 14-day zero-copy backup clone created`** |
* **🔍 BigQuery Studio Verification SQL:**
  ```sql
  SELECT column_name, data_type, is_partitioning_column
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.INFORMATION_SCHEMA.COLUMNS`
  WHERE table_name = 'audit_logs_unpartitioned'
    AND is_partitioning_column = 'YES';
  ```
  * **Expected Output:**
    | column_name | data_type | is_partitioning_column |
    | :--- | :--- | :--- |
    | `log_timestamp` | `TIMESTAMP` | `YES` |

* **The S0–S9 State Machine Execution Under the Hood:**
  ```
  S0: Snapshot Table IAM Policies & Row Access Policies via REST API
  S1: Pause Data Transfer Service Scheduled Queries (prevents write collisions)
  S2: CREATE TABLE audit_logs_unpartitioned__bqopt_bak_20260813 CLONE audit_logs_unpartitioned; ($0 backup)
  S3: CREATE TABLE audit_logs_unpartitioned__bqopt_new PARTITION BY DATE(log_timestamp) AS ...
  S4: INSERT INTO audit_logs_unpartitioned__bqopt_new SELECT * FROM audit_logs_unpartitioned;
  S5: CHECKSUM VALIDATION:
      Verify: COUNT(*) match AND BIT_XOR(FARM_FINGERPRINT(TO_JSON_STRING(t))) match!
  S6: ATOMIC METADATA SWAP:
      ALTER TABLE audit_logs_unpartitioned RENAME TO audit_logs_unpartitioned__bqopt_old;
      ALTER TABLE audit_logs_unpartitioned__bqopt_new RENAME TO audit_logs_unpartitioned;
  S7: Re-attach captured IAM Policies & Row-Level Security Policies to new table
  S8: Unpause Data Transfer Service Scheduled Queries
  S9: Write rollback_plan_json to optimizer_ops.change_sets
  ```

---

#### 🎯 Demo Case B: Date-Sharded Table Consolidation (`C3-02`)
* **Target Table Family:** `demo_ecommerce.ga_sessions_20260701..30` (30 individual tables)
* **💡 Plain English Explanation (What We Are Doing & Why):**
  > **What We Are Doing:** Merging 30 separate daily tables (`ga_sessions_20260701`, `ga_sessions_20260702`, ...) into one single partitioned table, while automatically generating a view for 100% backwards compatibility with legacy queries.  
  > **Why We Are Doing It:** Think of it like combining 30 separate thin notebooks into a single binder with labeled date tabs. It eliminates BigQuery metadata planning overhead on wildcard queries (`ga_sessions_*`) and makes queries run faster.

* **Evidence Comparison (Current vs. Proposed):**
  | Metric / Dimension | Current State (What it is today) | Proposed State (What we are changing it to) |
  | :--- | :--- | :--- |
  | **Table Structure** | `30 individual tables (ga_sessions_YYYYMMDD)` | `1 unified table: ga_sessions (Partitioned by DATE)` |
  | **Query Planning Overhead**| `High metadata latency on wildcard queries (*)` | `Instant single-table metadata pruning` |
  | **Compatibility** | `Queries use ga_sessions_*` | **`Auto-creates ga_sessions_* view for 100% backwards compatibility`** |
* **Underlying SQL Execution:**
  ```sql
  CREATE TABLE `<YOUR_PROJECT_ID>.demo_ecommerce.ga_sessions`
  PARTITION BY PARSE_DATE('%Y%m%d', visit_date)
  AS SELECT * FROM `<YOUR_PROJECT_ID>.demo_ecommerce.ga_sessions_*`;
  ```
* **🔍 BigQuery Studio Verification SQL:**
  ```sql
  SELECT table_name, partition_id, total_rows
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.INFORMATION_SCHEMA.PARTITIONS`
  WHERE table_name = 'ga_sessions'
  ORDER BY partition_id DESC
  LIMIT 5;
  ```
  * **Expected Output:**
    | table_name | partition_id | total_rows |
    | :--- | :--- | :--- |
    | `ga_sessions` | `20260730` | `100` |
    | `ga_sessions` | `20260729` | `100` |
    | `ga_sessions` | `20260728` | `100` |

---

#### 🎯 Demo Case C: Cold Data Archival to GCS Parquet & Drop (`C3-05`)
* **Target Table:** `demo_ecommerce.temp_staging_inactive`
* **💡 Plain English Explanation (What We Are Doing & Why):**
  > **What We Are Doing:** Exporting unused, dead tables (0 queries in 90+ days) to low-cost Google Cloud Storage (GCS Parquet format) and dropping them from BigQuery after retaining a $0 14-day zero-copy backup clone.  
  > **Why We Are Doing It:** Think of it like moving old boxes from an expensive downtown storage unit into a cheap suburban warehouse. You stop paying high BigQuery storage fees ($20/GB/year $\rightarrow$ $0), but retain a 14-day safety net to restore the table instantly if someone needs it.

* **Evidence Comparison (Current vs. Proposed):**
  | Metric / Dimension | Current State (What it is today) | Proposed State (What we are changing it to) |
  | :--- | :--- | :--- |
  | **Usage in Last 90 Days**| `0 Read Jobs, 0 Write Jobs in 90+ days` | `Exported to Coldline GCS Parquet` |
  | **BigQuery Storage Cost**| `$20.00 / GB / year` | **`$0.00 in BigQuery Storage`** |
  | **Safety Net** | `Permanent deletion risk` | **`14-day zero-copy clone retained in BigQuery before drop`** |
* **Underlying SQL Execution:**
  ```sql
  -- Step 1: Export compressed Parquet to GCS archive bucket
  EXPORT DATA OPTIONS (
    uri = 'gs://<YOUR_PROJECT_ID>-bq-archive/demo_ecommerce/temp_staging_inactive/*.parquet',
    format = 'PARQUET',
    compression = 'SNAPPY',
    overwrite = true
  ) AS SELECT * FROM `<YOUR_PROJECT_ID>.demo_ecommerce.temp_staging_inactive`;

  -- Step 2: Create 14-day zero-copy rollback clone
  CREATE TABLE `<YOUR_PROJECT_ID>.demo_ecommerce.temp_staging_inactive_backup`
  CLONE `<YOUR_PROJECT_ID>.demo_ecommerce.temp_staging_inactive`
  OPTIONS (expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 14 DAY));

  -- Step 3: Drop original table
  DROP TABLE `<YOUR_PROJECT_ID>.demo_ecommerce.temp_staging_inactive`;
  ```
* **🔍 BigQuery Studio Verification SQL:**
  ```sql
  SELECT table_name, table_type
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.INFORMATION_SCHEMA.TABLES`
  WHERE table_name LIKE 'temp_staging_inactive%';
  ```
  * **Expected Output:**
    | table_name | table_type |
    | :--- | :--- |
    | `temp_staging_inactive_backup` | `CLONE` |

---

### 4️⃣ Class 4: SQL Anti-Pattern Detection & Git Pull Requests
*(Reference: Design Document §5.4)*

* **💡 Plain English Explanation (What We Are Doing & Why):**
  > **What We Are Doing:** Scanning SQL code for inefficient habits (like `SELECT *`, `DATE()` functions wrapping timestamp columns, or unindexed `CROSS JOIN`s) and generating ready-to-merge Git Pull Requests for developer codebases.  
  > **Why We Are Doing It:** Think of it like an automated spell-checker for database code. Fixing bad SQL habits directly inside the source code repository prevents developers from accidentally launching queries that scan hundreds of gigabytes unnecessarily.

* **🤖 Delivery Route Callout (`CI_PULL_REQUEST` — Automatic vs Manual):**
  > **Why `CI_PULL_REQUEST` instead of `DIRECT_GUARDED`?**  
  > Class 1, 2, and 3 changes modify BigQuery tables directly via DDL. Class 4 optimizations target **application & ETL source code** (dbt models, Airflow DAGs, Looker views, Python/Java services) residing in Git repositories.  
  > 
  > **Is it Automatic or Manual?**  
  > * **100% Automated Detection & SQL Rewrite:** The engine automatically detects the wasteful query, calculates scanned column waste, and generates the fully optimized rewritten SQL query.  
  > * **Automated Git Integration (CI/CD):** When Git integration (GitHub Actions, GitLab CI, or Bitbucket Webhooks) is configured, approving the change set **automatically opens a Git Pull Request** against the developer's repository.  
  > * **Manual Governance Mode:** If Git auto-merge is disabled, developers simply copy the generated SQL snippet directly from the Web Review UI card into their repository.

* **💻 How to Demonstrate GitHub PR Generation in Your Demo (Two Interactive Ways):**
  
  * **Option 1: 1-Click Interactive GitHub PR Modal (In Web Review UI):**
    1. Open the Review UI at **`http://localhost:8080`**.
    2. Scroll down to any Class 4 card (e.g. `[C4-01] SELECT * Column Projection Pruning`).
    3. Click the **`🐙 View GitHub PR`** button next to "Approve change".
    4. A realistic **GitHub Pull Request Modal** opens on screen showing:
       * 🟢 Branch: `main` $\leftarrow$ `bqopt/c4-01-audit-logs-pruning`
       * 💰 Estimated Monthly Savings: **$44.00 / month** (85% scan reduction)
       * 📝 Unified Code Diff (Red `-` vs. Green `+`)
       * 🛡️ Automated CI Checks: BigQuery Dry-Run Passed, Cost Reduction Verified.
       * 📋 Button: `Copy GitHub CLI Command`

  * **Option 2: Live Terminal Git PR Emitter:**
    Run the PR emitter script directly in your terminal tab to show automated CI/CD branch and diff creation:
    ```bash
    python3 scripts/emit_github_pr.py C4-01
    # or test with C4-03 (date-wrap) or C4-05 (cross-join):
    # python3 scripts/emit_github_pr.py C4-03
    ```

* **🔍 BigQuery Studio Verification SQL (View All Class 4 Change Sets):**
  ```sql
  SELECT 
    change_set_id,
    target_table AS query_family,
    rule_ids,
    execution_route,
    net_monthly_value_usd,
    confidence,
    score
  FROM `<YOUR_PROJECT_ID>.optimizer_ops.change_sets`
  WHERE apply_class = 4
  ORDER BY score DESC;
  ```

  * **Expected Output:**
    | change_set_id | query_family | rule_ids | execution_route | net_monthly_value_usd | confidence | score |
    | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
    | `c401-audit-logs-hash` | `hash_logs_audit_query` | `["C4-01"]` | `CI_PULL_REQUEST` | `44.00` | `0.90` | `39.60` |
    | `c403-date-wrap-hash` | `hash_date_wrap_query` | `["C4-03"]` | `CI_PULL_REQUEST` | `29.00` | `0.95` | `27.55` |
    | `c405-cross-join-hash` | `hash_cross_join_query` | `["C4-05"]` | `CI_PULL_REQUEST` | `35.00` | `0.85` | `29.75` |

---

#### 🎯 Demo Case A: `SELECT *` Column Projection Expansion (`C4-01`)
* **Target Query Hash:** `hash_logs_audit_query`
* **Before (Anti-Pattern - Scans All Unneeded Columns):**
  ```sql
  SELECT * 
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.audit_logs_unpartitioned`
  WHERE log_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY);
  ```
* **After (Optimized Remediation in Git PR):**
  ```sql
  SELECT log_id, actor_email, action, ip_address, log_timestamp
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.audit_logs_unpartitioned`
  WHERE log_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY);
  ```
* **Live Execution Benchmark Breakdown:**

  | Metric | 🔴 Before (`SELECT *`) | 🟢 After (Explicit Columns) | 🏆 Optimization Impact |
  | :--- | :--- | :--- | :--- |
  | **Slot Milliseconds** | `32 slot-ms` | `16 slot-ms` | **🔥 50.0% COMPUTE REDUCTION** |
  | **Scanned Bytes** | Full table width scan | Only referenced columns | **85% Byte Reduction on wide tables** |
  | **Execution Duration** | `291 ms` | `268 ms` | **Faster execution** |

---

#### 🎯 Demo Case B: Non-Sargable Date Function in WHERE (`C4-03`)
* **Target Query Hash:** `hash_date_wrap_query`
* **Before (Anti-Pattern - Defeats Partition Pruning, Full Table Scan):**
  ```sql
  SELECT order_id, order_amount 
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.orders`
  WHERE DATE(created_at) = CURRENT_DATE();
  ```
* **After (Optimized Partition-Pruned Filter in Git PR):**
  ```sql
  SELECT order_id, order_amount 
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.orders`
  WHERE order_date = CURRENT_DATE();
  ```
* **Live Execution Benchmark Breakdown:**

  | Metric | 🔴 Before (`DATE(created_at)`) | 🟢 After (`order_date = ...`) | 🏆 Optimization Impact |
  | :--- | :--- | :--- | :--- |
  | **Bytes Processed** | **`527.34 KB`** (30 partitions) | **`17.56 KB`** (1 partition) | **🔥 96.7% BYTE SCAN REDUCTION** |
  | **Slot Milliseconds** | **`94 slot-ms`** | **`15 slot-ms`** | **⚡ 84.0% COMPUTE REDUCTION** |
  | **Execution Duration** | `277 ms` | `240 ms` | **Partition Pruning Restored** |

---

#### 🎯 Demo Case C: Cartesian `CROSS JOIN` Elimination (`C4-05`)
* **Target Query Hash:** `hash_cross_join_query`
* **Before (Anti-Pattern - Memory & Slot Explosion):**
  ```sql
  SELECT o.order_id, e.event_id 
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.orders` o 
  CROSS JOIN `<YOUR_PROJECT_ID>.demo_ecommerce.clickstream_events` e;
  ```
* **After (Optimized Remediation in Git PR):**
  ```sql
  SELECT o.order_id, e.event_id 
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.orders` o 
  INNER JOIN `<YOUR_PROJECT_ID>.demo_ecommerce.clickstream_events` e 
    ON o.customer_id = e.user_id AND o.order_date = e.event_date;
  ```
* **Live Execution Benchmark Breakdown:**

  | Metric | 🔴 Before (`CROSS JOIN`) | 🟢 After (`INNER JOIN`) | 🏆 Optimization Impact |
  | :--- | :--- | :--- | :--- |
  | **Slot Milliseconds (CPU Compute Cost)** | **`199,439 slot-ms`** | **`153 slot-ms`** | **🔥 99.92% COMPUTE REDUCTION (1,303× less CPU!)** |
  | **Execution Duration** | **`10 sec 663 ms`** | **`383 ms`** | **⚡ 28× FASTER (Sub-second execution)** |
  | **Query Insights / Diagnostics** | ⚠️ `High Cardinality Join` | *Clean (No Warnings)* | **Cartesian Memory Explosion Eliminated** |
  | **Bytes Billed** | `20 MB` | `20 MB` | Minimum BigQuery charge |
  | **Bytes Processed** | `556.64 KB` | `817.59 KB` | +260 KB (Reads join keys from disk) |

---

#### 🎯 Demo Case D: `NOT IN` Subquery to `NOT EXISTS` Anti-Join (`C4-06`)
* **Target Query Hash:** `hash_notin_query`
* **Before (Anti-Pattern - Dangerous NULL Evaluation Trap):**
  ```sql
  SELECT order_id, customer_id, order_amount 
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.orders`
  WHERE customer_id NOT IN (
    SELECT user_id FROM `<YOUR_PROJECT_ID>.demo_ecommerce.clickstream_events`
  );
  ```
* **After (Optimized Correlation Anti-Join in Git PR):**
  ```sql
  SELECT o.order_id, o.customer_id, o.order_amount 
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.orders` o 
  WHERE NOT EXISTS (
    SELECT 1 
    FROM `<YOUR_PROJECT_ID>.demo_ecommerce.clickstream_events` e 
    WHERE e.user_id = o.customer_id
  );
  ```
* **Live Execution Benchmark Breakdown:**

  | Metric | 🔴 Before (`NOT IN`) | 🟢 After (`NOT EXISTS`) | 🏆 Optimization Impact |
  | :--- | :--- | :--- | :--- |
  | **Slot Milliseconds** | **`250 slot-ms`** | **`162 slot-ms`** | **🔥 35.2% COMPUTE REDUCTION** |
  | **NULL Safety Guard** | ⚠️ Fails/Nulls on 1 NULL | *100% Robust NULL-Safe* | **Prevents silent pipeline corruption** |

---

#### 🎯 Demo Case E: Non-Deterministic Cache-Buster Removal (`C4-08`)
* **Target Query Hash:** `hash_cache_buster_query`
* **Before (Anti-Pattern - Volatile CURRENT_TIMESTAMP in SELECT):**
  ```sql
  SELECT order_id, order_amount, CURRENT_TIMESTAMP() AS pulled_at 
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.orders`
  WHERE order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY);
  ```
* **After (Optimized Cache-Friendly SQL in Git PR):**
  ```sql
  SELECT order_id, order_amount 
  FROM `<YOUR_PROJECT_ID>.demo_ecommerce.orders`
  WHERE order_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY);
  ```
* **Live Execution Benchmark Breakdown:**

  | Metric | 🔴 Before (Volatile Timestamp) | 🟢 After (Deterministic SQL) | 🏆 Optimization Impact |
  | :--- | :--- | :--- | :--- |
  | **Subsequent Runs Cost** | Billed every run ($) | **$0.00 (100% Cache Hit)** | **🔥 100% FREE DASHBOARD REFRESHES** |
  | **Slot Milliseconds** | 55 slot-ms per run | **0 slot-ms (Served from cache)** | **0 CPU slots consumed** |

---

#### 🎯 Demo Case F: Single-Node `ORDER BY` Elimination in CTE (`C4-09`)
* **Target Query Hash:** `hash_orderby_query`
* **Before (Anti-Pattern - Single-Node Sort Bottleneck):**
  ```sql
  WITH sorted_orders AS (
    SELECT customer_id, order_amount 
    FROM `<YOUR_PROJECT_ID>.demo_ecommerce.orders` 
    ORDER BY order_amount DESC
  ) 
  SELECT customer_id, AVG(order_amount) 
  FROM sorted_orders 
  GROUP BY 1;
  ```
* **After (Optimized Remediation in Git PR):**
  ```sql
  WITH sorted_orders AS (
    SELECT customer_id, order_amount 
    FROM `<YOUR_PROJECT_ID>.demo_ecommerce.orders`
  ) 
  SELECT customer_id, AVG(order_amount) 
  FROM sorted_orders 
  GROUP BY 1;
  ```
* **Live Execution Benchmark Breakdown:**

  | Metric | 🔴 Before (Useless Sort) | 🟢 After (Distributed Execution) | 🏆 Optimization Impact |
  | :--- | :--- | :--- | :--- |
  | **Slot Architecture** | Forces Single-Node Final Sort | **100% Parallel Distributed** | **Eliminates cluster worker bottlenecks** |
  | **Query Correctness** | Exact same average result | **Exact same average result** | **Zero business metric change** |

---

## ⚙️ 5. Step-by-Step State Machine & Tables Affected Matrix

This matrix shows **exactly what tables and states are touched** at each step:

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 CONTROL PLANE LIFECYCLE STATE MACHINE                                  │
├───────────────────┬───────────────────────────────────┬────────────────────────────────────────────────┤
│ ACTION / TRIGGER  │ BIGQUERY TABLES READ              │ BIGQUERY TABLES & FIELDS MODIFIED              │
├───────────────────┼───────────────────────────────────┼────────────────────────────────────────────────┤
│ 1. CLI `rules`    │ • jobs_events                     │ • optimizer_ops.change_sets                    │
│    (Rules Engine) │ • table_state_daily               │   - Inserts new rows with state='PENDING_REVIEW'│
│                   │ • columns_daily                   │   - Computes gross_monthly_savings_usd         │
│                   │ • v_query_families_28d            │   - Sets confidence = d_sum * d_win * d_vol    │
│                   │ • v_dataset_storage_billing_gap   │   - Generates proposed_change_json DDL payload │
├───────────────────┼───────────────────────────────────┼────────────────────────────────────────────────┤
│ 2. Web UI Click   │ • optimizer_ops.change_sets       │ • optimizer_ops.change_sets                    │
│    "Approve"      │ • v_query_families_28d            │   - state: 'PENDING_REVIEW' ──► 'APPROVED'     │
│                   │                                   │   - approvals: appends [principal, timestamp]  │
│                   │                                   │   - verification_plan_json: freezes 28-day     │
│                   │                                   │     baseline (p50/p95 latency, slot-ms, bytes) │
│                   │                                   │ • Google Active Assist API: Marks as CLAIMED   │
├───────────────────┼───────────────────────────────────┼────────────────────────────────────────────────┤
│ 3. CLI `execute`  │ • optimizer_ops.v_approved_ready  │ • optimizer_ops.change_sets                    │
│    (Executor)     │                                   │   - state: 'APPROVED' ──► 'APPLYING' ──►       │
│                   │                                   │     'APPLIED' ──► 'VERIFYING'                  │
│                   │                                   │   - applied_at = CURRENT_TIMESTAMP()           │
│                   │                                   │   - rollback_plan_json = [captured backup spec]│
│                   │                                   │ • Workload Tables:                             │
│                   │                                   │   - Class 1: ALTER TABLE SET CLUSTER BY...     │
│                   │                                   │   - Class 3: S0-S9 Copy-Swap-Rebind execution  │
├───────────────────┼───────────────────────────────────┼────────────────────────────────────────────────┤
│ 4. CLI `verify`   │ • jobs_events (post-apply queries)│ • optimizer_ops.change_sets                    │
│    (CFO Verifier) │ • change_sets (frozen baseline)   │   - state: 'VERIFYING' ──► 'VERIFIED'          │
│                   │                                   │   - realized_over_predicted = Realized / Pred  │
│                   │                                   │   - verification_result_json = [Receipt Telemetry]│
│                   │                                   │ • optimizer_ops.rule_accuracy                  │
│                   │                                   │   - Updates rolling mean for d_history factor  │
│                   │                                   │ • optimizer_ops.v_receipts (Live CFO Receipt)  │
├───────────────────┼───────────────────────────────────┼────────────────────────────────────────────────┤
│ 5. CLI `rollback` │ • optimizer_ops.change_sets       │ • optimizer_ops.change_sets                    │
│    (Rollback)     │   (reads rollback_plan_json)      │   - state: 'VERIFYING' ──► 'ROLLED_BACK'       │
│                   │                                   │ • Workload Tables:                             │
│                   │                                   │   - Renames current table ──► _regressed       │
│                   │                                   │   - Renames zero-copy backup clone ──► target  │
│                   │                                   │   - Re-attaches original IAM & RLS policies    │
└───────────────────┴───────────────────────────────────┴────────────────────────────────────────────────┘
```

---

## 🔄 6. Resetting the Demo to a Clean / Virgin State

When you are done with a demo or want to rehearse again from scratch, you have two reset options:

### Option 1: Standard Demo Reset (Keep Control Plane, Reset Demo Workload)
Deletes `demo_ecommerce` and `demo_scratch` and clears all telemetry from `optimizer_ops`, leaving the schemas ready for an immediate re-run:
```bash
./scripts/demo_cleanup.sh
# or: python3 scripts/demo_cleanup.py
```

### Option 2: 100% Full Teardown to Virgin State (Complete Wipe)
Completely wipes **everything** in GCP—deletes the `demo_ecommerce` dataset, `demo_scratch` dataset, and the entire `optimizer_ops` control plane dataset:
```bash
./scripts/demo_cleanup.sh --wipe-ops
# or: python3 scripts/demo_cleanup.py --wipe-ops
```
*After a full teardown, running `./scripts/demo_setup.sh` (or `python3 scripts/demo_setup.py`) will rebuild the entire environment from absolute scratch.*

---

## 💬 7. Tough Customer Questions & Winning Answers

### Q1: *"How is this different from Atlan or Google Active Assist?"*
> **Answer:** *"Atlan and Active Assist are opportunistic reporting tools—they show you a list of 10,000 potential problems in isolation but give you zero tools to execute them safely. Our Control Plane is an **execution and governance engine**: it enforces Workload-Capped Honest Scoring to prevent inflated savings claims, creates $0 zero-copy backup clones, preserves IAM/RLS policies, and provides 1-click safe execution with closed-loop CFO proof receipts."*

### Q2: *"Will running table rebuilds break our Looker dashboards or ETL queries?"*
> **Answer:** *"No. We use a Copy-Swap-Rebind pattern. Looker and ETL continue querying the production table while the new table is built in staging. The switch happens via an atomic `ALTER TABLE RENAME` metadata swap that takes less than 200 milliseconds. If any error occurs during build, the staging table is discarded and production is never touched."*

### Q3: *"How much does running the Control Plane cost?"*
> **Answer:** *"Almost zero. The collector reads BigQuery `INFORMATION_SCHEMA` metadata (which is free in Google Cloud). The ops tables consume a few megabytes of storage, and all backup clones are Zero-Copy Clones ($0 storage until altered)."*

### Q4: *"How does the Control Plane help us decide between On-Demand billing and BigQuery Editions commitments?"*
> **Answer:** *"Via the `jobs_hourly_slots` telemetry table and Rule `W-01`. The collector archives hourly slot concurrency curves from `JOBS_TIMELINE` and presents side-by-side interactive DDL options right on the UI card: **Option 1 (100-Slot Baseline + 200 Autoscaling Burst = $3,850/mo)** for steady 24/7 workloads, and **Option 2 (0-Slot Baseline + Pure Autoscaling = $1,170/mo)** for spiky daytime workloads with $0 overnight idle cost."*

### Q5: *"Wait — if we enforce a Proactive Cost Guardrail (`W-02` / `maximum_bytes_billed`) to stop a $5,000 runaway query before it runs, won't that fail our nightly ETL pipelines or Service Accounts?"*
> **Answer:** *"Never — and that is why our optimizer explicitly separates **Human Ad-Hoc Users** from **Service Accounts & Production ETL** in `INFORMATION_SCHEMA.JOBS` (`jobs_events.user_email`). Every query in BigQuery stamps `user_email`. Rule `W-02` inspects `user_email` and splits workloads into two isolated lanes:*
> *1. **👤 Human Ad-Hoc Analysts (`user_email NOT LIKE '%.gserviceaccount.com'`)**: Enforces a **50 GiB per-query safety cap (`@@maximum_bytes_billed = 53687091200`, max ~$0.31/query)** and assigns an isolated **50-slot autoscaling sandbox (`human_adhoc_sandbox_pool`)** so an accidental `SELECT *` without a `WHERE` clause is blocked in 0 milliseconds before scanning 500 TiB.*
> *2. **🤖 Service Accounts & Production ETL (`*.iam.gserviceaccount.com`, Airflow, dbt, Dataform)**: Marked **100% EXEMPT** from query byte caps and assigned to the dedicated production reservation pool (`enterprise_prod_pool`) so nightly 5 TB–10 TB data transformations never fail."*

