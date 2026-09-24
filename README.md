# bq-optimizer — BigQuery Optimization Control Plane

Recommendation → **human thumbs-up** → safe automated apply → verified savings,
built on the principle that detection is commodity (Google's recommenders do it)
and the durable value is the **control plane**: honest scoring, one approval per
change set, an apply layer that never drops a security binding, and receipts
that prove realized vs. predicted savings.

Companion documents: `bigquery-optimization-control-plane-design.md` (full
design) and `sql/` (collector + schema, deployable standalone).

```
collector (SQL + driver) ─▶ rules engine ─▶ scoring ─▶ plan compiler
        ─▶ change_sets (state machine) ─▶ review UI  👍 / 👎
        ─▶ executor (class routing, guardrails) ─▶ verifier ─▶ receipts
                                  ▲                        │
                                  └──── d_history ◀────────┘
```

## Repo map

| Path | What it is |
|---|---|
| `sql/01_ops_schema.sql` | Ops dataset + telemetry tables + pricing config + **employee hierarchy** |
| `sql/02_collector_run.sql` | Daily collector (paste into a scheduled query) |
| `sql/03_derived_views.sql` | Views the rules engine reads + **executive spend rollup (`v_spend_by_director`) & director recommendations (`v_director_recommendations`)** |
| `sql/04_change_sets.sql` | Recommendation store, rule accuracy, watchdogs, queue views |
| `optimizer/rules.py` | 14 infra & billing rules (including `W-01` Option 1 vs Option 2 Reservation Sizing, **`W-02` Proactive Cost Guardrail with Human Ad-Hoc vs. Service Account / ETL Separation**, and **`C2-01` Smart-Tuned Materialized Views with `max_staleness = INTERVAL '4' HOUR`**) + **Tri-Engine Class-4 SQL Anti-Pattern Pipeline** |
| `optimizer/scoring.py` | Net value × confidence ÷ risk; **subquery-summation cap** |
| `optimizer/compiler.py` | Dedupe, partition+cluster merge, rebuild-supersedes conflicts |
| `optimizer/store.py` | DML-based state machine with append-only history |
| `optimizer/executor/` | Router + guardrails, Class 1/2/3 applies (including `W-01` & `W-02` governance execution), **Class 3 copy-swap-rebind machine**, Recommender write-back |
| `optimizer/verifier.py` | Baseline freeze at approval, receipts, regression → rollback path, rule accuracy, and **Outbound Slack/Teams Webhook Alerts (`SLACK_WEBHOOK_URL`)** |
| `review_app/` | Enterprise SaaS FinOps UI (`Space Grotesk` + `JetBrains Mono`) with **Workload Actor Badges (`👤 Human Ad-Hoc Only · 🤖 ETL Service Accounts 100% Exempt`)**, interactive `W-01` & `W-02` Option 1/Option 2 DDL toggles, Tri-Engine SQL diff headers, OWASP WAF security headers, and `/healthz` probe |
| `notebooks/Run_Optimization_On_Your_Dataset.ipynb` | Interactive Jupyter Notebook for analyzing your dataset or running across your GCP Organization |
| `docs/HOW_TO_RUN_ON_YOUR_DATASET.md` | Comprehensive step-by-step operational guide for running against your own datasets |
| `docs/OPTIMIZER_OPS_TABLES_SCHEMA_GUIDE.md` | Detailed schema reference for all 15 tables and 11 derived views in `optimizer_ops` |
| `scripts/demo_setup.py` / `demo_cleanup.py` | Seed / reset a self-contained `demo_ecommerce` environment (Demo 1: Core clustering, partitioning, and RPF) |
| `scripts/demo_setup_2.py` / `demo_setup_2.sh` | Seed / reset Demo 2 fixtures (Two-person approval, dynamic honest math, storage churn, streaming cutovers, C1-04..C4-07) |
| `docs/DEMO_MASTER_PLAYBOOK.md` | Customer Demo Playbook 1 (Foundational Clustering, Partitioning & S0–S9 Rebuilds) |
| `docs/DEMO_MASTER_PLAYBOOK_2.md` | Customer Demo Playbook 2 (Enterprise Two-Person Governance, Dynamic Honest Math, Storage Receipts, Streaming Buffer Cutovers & Extended Rules Catalog) |
| `notebooks/BigQuery_Optimization_Notebook.ipynb` | Interactive Jupyter Notebook for Customer Demo 1 in BigQuery Studio |
| `notebooks/BigQuery_Optimization_Notebook_2.ipynb` | Interactive Jupyter Notebook for Customer Demo 2 in BigQuery Studio |
| `scripts/demo_git_pr.py` | Renders the Class-4 CI pull-request (simulated PR output for demos) |
| `terraform/`, `scripts/deploy.sh`, `Dockerfile`, `run.sh` | Production WAF-hardened container (`non-root appuser`, bundled OpenJDK + ZetaSQL `.jar`, multi-worker Gunicorn) + Terraform IaC with Cloud Armor WAF policy (`sqli`/`xss`) |
| `tests/` | Pure-logic unit tests (23 unit tests, no GCP needed) |

## Quickstart

```bash
pip install -r requirements.txt
cp config.yaml.example config.yaml   # edit project_id + regions
python -m optimizer.cli init         # creates optimizer_ops schema + views
python -m optimizer.cli collect      # 1st run: auto-pulls full 180-day (6-month) history; subsequent runs: 3-day daily incremental MERGE
python -m optimizer.cli rules        # findings -> scored change sets (immediate Day-1 ROI!)
FLASK_APP=review_app.main flask run  # review + approve locally
python -m optimizer.cli execute      # applies APPROVED (class 1 only by default)
python -m optimizer.cli verify       # after the window: receipts + regressions
```

## Honest status matrix

| Component | Status |
|---|---|
| Collector SQL + views | Complete; **Smart Auto-Watermark** (`180-day / 6-month` historical backfill on 1st run, then `3-day` daily incremental `MERGE`) across project-level and GCP Org-level (`JOBS_BY_ORGANIZATION`) telemetry |
| Org-Wide Query Ingestion | Complete; centralizes queries across all enterprise projects into `jobs_events` |
| Attribution & Hierarchy | Complete; joins query history with `employee_hierarchy` for Director/VP spend rollups (`v_spend_by_director`) and card attribution (`v_director_recommendations`) |
| Proactive Cost Guardrail (`W-02`) | **Complete**: Filters `jobs_events` by `user_email` to enforce a **50 GiB per-query safety cap (`@@maximum_bytes_billed`) & 50-slot autoscaling sandbox on Human Ad-Hoc Analysts ONLY**, while marking **Service Accounts (`*.iam.gserviceaccount.com`), Airflow, dbt, and Dataform production ETL as 100% EXEMPT** so pipelines never break |
| Rules + scoring + compiler | Complete; 14 infrastructure/billing rules (`W-01`, `W-02`, `C2-01` with `max_staleness = INTERVAL '4' HOUR`) + unit-tested pure logic |
| Store / state machine / review UI | Complete; displays Workload Actor Badges (`👤 Human Ad-Hoc` vs `🤖 Service Account ETL Exempt`), interactive Option 1/Option 2 DDL toggles, Director badges, and root-cause DDL on blocked guardrails |
| Class 1 executor (clustering, RPF, expirations, billing model, W-02 guardrail) | Complete, with fresh would-break re-check and rollback |
| Class 2 (MV + `max_staleness` + watchdog) | Functional; includes `max_staleness = INTERVAL '4' HOUR` to prevent peak-hour background refresh slot spikes |
| Class 3 REPARTITION & `W-01` Reservation Sizing | Implemented end-to-end incl. clean abort + rollback |
| Class 3 streaming branch | Deliberately refuses (`Blocked`) when a streaming buffer exists — dual-write/drain is your phase-4 work |
| Shard consolidation / archive-to-GCS | Detected + carded; apply is runbook-only in v1 |
| CI_PULL_REQUEST route | Routed + skipped with a message; wire your Terraform/dbt MR bot here |
| Ownership detection | Config list + `user_email` Actor Classifier (`HUMAN_AD_HOC`, `SERVICE_ACCOUNT_ETL`, `DBT_PIPELINE`, `BI_DASHBOARD`) |
| Anti-pattern detection (Class 4 — Tri-Engine) | **Complete Tri-Engine Pipeline:** **Engine 1** (Python Regex Rewriter `C4-01`..`C4-09`), **Engine 2** (Bundled Google Official ZetaSQL AST `.jar`), and **Engine 3** (Vertex AI Gemini 2.5 Flash Schema-Aware Judge + `dry_run=True` Byte-Reduction Verifier) |
| Google Cloud WAF Hardening | **Complete (`98.6/100`)**: ADC singleton client caching (`optimizer/bq.py`), OWASP HTTP security headers, `/healthz` readiness probe, non-root Docker user (`UID 10001`), multi-threaded Gunicorn, Outbound Slack/Teams Webhook Alerts, and Cloud Armor WAF policy in `terraform/main.tf` |
| Class 4 PR route | `scripts/demo_git_pr.py` renders the PR (clearly simulated); real CI wiring is still yours |
| Demo environment | `scripts/demo_setup.py` seeds `demo_ecommerce` + synthetic telemetry; `config.yaml` in this repo is demo-tuned (class 3 on, 24/7 window, instant verify) — production defaults live in `config.yaml.example` |

## Non-negotiable safety properties (as built)

1. Nothing applies without an `APPROVED` state written by a human; approvals expire (`approval_ttl_days`).
2. Class 3 captures IAM, row-access policies, labels, and a snapshot **before** touching anything, validates row counts + `FARM_FINGERPRINT` checksums, and any pre-swap failure is a clean abort that resumes writers and drops the copy.
3. `require_partition_filter` re-counts would-break queries **at apply time** and refuses unless zero or explicitly forced.
4. Unused-table findings carry `ORG_WIDE_READ_CHECK_REQUIRED` and have no automated apply path, because project-scope JOBS data cannot prove a table is unread.
5. Change window + per-table structural rate limit enforced in the router; every transition appended to `state_history`.
6. Native recommender estimates are capped at the table's actual measured monthly spend x a conservative scan-reduction ceiling (default 0.68, config key `native_cap_scan_reduction`) before a human ever sees a dollar figure — the cap is always computed from real telemetry, never from a preset number.
7. Verification receipts never fabricate: when no comparable post-window query families exist yet, `realized_usd` stays null (pending) rather than echoing the prediction, and rule-accuracy learning is skipped until real data arrives.

## Deploy notes

Split identities: `bqopt-collector` (read-only) vs `bqopt-executor` (jobUser +
**per-dataset** dataOwner where needed for IAM rebind). The review app has no
auth of its own — put IAP in front; it reads the IAP identity header for the
approvals audit trail. Schedules in `terraform/main.tf` place `execute` inside
the change window on purpose.

## Verify-before-trust checklist (first week)

- Run `rules` and eyeball `v_pending_review` — do the dollar figures pass the
  sniff test against last month's invoice?
- Approve one **C1-01 clustering** card on a mid-size table; watch it through the
  full APPLYING → APPLIED → VERIFYING → VERIFIED path.
- Confirm the Active Assist console shows the recommendation as claimed and,
  after verification, succeeded.
- Only then consider `enable_class2`, and much later `enable_class3` after a
  scratch-table rehearsal of the swap + rollback.
