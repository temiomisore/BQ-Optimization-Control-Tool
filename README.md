# bq-optimizer — BigQuery Optimization Control Plane

Recommendation → **human thumbs-up** → safe automated apply → verified savings,
built on the principle that detection is commodity (Google's recommenders do it)
and the durable value is the **control plane**: honest scoring, one approval per
change set, an apply layer that never drops a security binding, and receipts
that prove realized vs. predicted savings.

Companion documents: `docs/bigquery-optimization-control-plane-design.md` (full
design), `docs/OPTIMIZER_OPS_TABLES_SCHEMA_GUIDE.md` & `docs/BIGQUERY_TABLES_CATALOG.md`
(**18 persistent tables & 16 analytical/queue views**), and `sql/` (collector + schema).

```
collector (SQL + multi-region/project courier) ─▶ rules engine ─▶ scoring ─▶ plan compiler
        ─▶ change_sets (state machine) ─▶ review UI  👍 / 👎 (+ /#settings & Run scan now)
        ─▶ executor (class routing, guardrails, PR hand-offs) ─▶ verifier ─▶ receipts
                                  ▲                                             │
                                  └───────────────── d_history ◀────────────────┘
```

## 1-Click Setup in Google Cloud (Zero Copy-Pasting or YAML Editing)

Click below to launch the **fully automated** setup in **Google Cloud Shell** (runs entirely in your browser — nothing to type or paste):

[![Open in Cloud Shell](https://gstatic.com/cloudssh/images/open-btn.svg)](https://console.cloud.google.com/cloudshell/editor?shellonly=true&cloudshell_image=gcr.io/ds-artifacts-cloudshell/deploystack_custom_image&cloudshell_git_repo=https://github.com/temiomisore/BQ-Optimization-Control-Tool&cloudshell_git_branch=main)

> **How it works (zero pasting):**
> 1. On the Cloud Shell prompt, check **Trust repo** and click **Confirm** *(press **Authorize** if Google asks)*.
> 2. Setup starts **automatically** — use your **`↑` / `↓` arrow keys + `Enter`** to select **which GCP project** to deploy into.
> 3. Everything else runs automatically (~4 minutes) until you see **`🎉 Setup Complete — Your App Is Live!`**, then click your `https://bqopt-review-ui-.../#settings` link!
>
> <details><summary>Optional: manual CLI command if you already have a regular Cloud Shell terminal open</summary>
>
> ```bash
> rm -rf BQ-Optimization-Control-Tool && git clone --depth=1 https://github.com/temiomisore/BQ-Optimization-Control-Tool.git && cd BQ-Optimization-Control-Tool && ./scripts/install.sh --yes
> ```
> </details>

What `scripts/install.sh` sets up automatically (~4 minutes):
1. **Auto-detects** your signed-in Google account (makes you **Settings Admin** + initial **FinOps Approver**), your GCP Project, and your GCP Organization ID.
2. **Enables required APIs** (`BatchEnable`) and creates a dedicated least-privilege service account (`bqopt-sa`).
3. **Deploys the Web UI** on Cloud Run behind **Direct Identity-Aware Proxy (`--iap`)** — no Load Balancer, SSL certificate, or DNS setup needed, with corporate `@domain` view access pre-bound.
4. **Initializes `optimizer_ops`** (18 tables + 16 views + `enable_info_schema_storage = TRUE` across discovered regions) and schedules the nightly pipeline (`bqopt-nightly-pipeline` + `bqopt-nightly-schedule`) at **`03:00 UTC`** (inside the `03:00–09:00 UTC` safe change window).
5. **Configure & scan in the browser:** Open `<your-app-url>/#settings` to verify permissions, pick HR / service-account owner tables and columns from live dropdowns, add teammates, and click **▶ Run scan now** right in the UI.

## Repo map

| Path | What it is |
|---|---|
| `sql/01_ops_schema.sql` | Ops dataset + **15 persistent tables**: query/slot telemetry, daily table/column/partition/dataset/object snapshots, Active Assist ingest, pricing config, **`employee_hierarchy`**, **`service_account_owners`**, **`regions_seen`**, and **`settings_changes`** |
| `sql/02_collector_run.sql` | Daily collector SQL for the home location & host project (`optimizer/remote_collector.py` extends this across all discovered regions and sibling projects) |
| `sql/03_derived_views.sql` | **13 analytical & attribution views**: `v_config`, **`v_reservation_rates`**, **`v_jobs_costed`** (`schema_version` stamp), `v_table_read_write_90d`, `v_query_families_28d`, `v_dataset_storage_billing_gap`, `v_unpartitioned_scan_targets`, `v_employee_hierarchy_src`, `v_service_account_owners_src`, **`v_principal_directory`**, **`v_unmapped_service_accounts`**, **`v_spend_by_director`**, and **`v_director_recommendations`** |
| `sql/04_change_sets.sql` | **3 governance tables** (`change_sets`, `rule_accuracy`, `cost_watchdogs`) + **3 queue/receipt views** (`v_pending_review`, `v_approved_ready`, `v_receipts`) |
| `optimizer/regions.py` & `optimizer/remote_collector.py` | Cloud Asset Inventory **region + multi-project discovery** (`regions_seen`), cross-region & cross-project courier (`_stg_*` 24h staging + `MERGE`), org-mode **`1b_query_previews` SQL text hydration**, and `TABLE_STORAGE_BY_ORGANIZATION` → per-project `TABLE_STORAGE` fallback |
| `optimizer/schema_guard.py` | Stamps a DDL `schema_version` hash label on `v_jobs_costed` and automatically self-heals `optimizer_ops` views/tables whenever code upgrades |
| `optimizer/rules.py` | 14 infra & billing rules (`W-01` Option 1 vs Option 2 Editions Sizing, **`W-02` Proactive Cost Guardrail with Human Ad-Hoc vs. Service Account / ETL Separation**, **`C2-01` Smart-Tuned Materialized Views with `max_staleness = INTERVAL '4' HOUR`**) + **Tri-Engine Class-4 SQL Anti-Pattern Pipeline** (Regex + ZetaSQL `.jar` + **Vertex AI `gemini-3.8-flash`** + cross-project schema-stub `dry_run=True` byte-reduction verifier) |
| `optimizer/scoring.py` & `optimizer/compiler.py` | Net value × confidence ÷ risk, **subquery-summation cap**, partition+cluster merge, conflict resolution, and **money-losing card suppression (`min_card_net_monthly_usd`)** |
| `optimizer/pricing.py` | **Billing-aware money**: on-demand jobs at bytes × \$/TiB, reservation jobs at slot-hours × edition rate; compounding of overlapping cards; headline capped at actual 30d compute spend |
| `optimizer/governance.py` & `optimizer/attribution.py` | **FinOps gate** (server-side 403 + executor re-check, verified IAP JWT identity) and configurable HR + **service-account → human owner → Director** view shims |
| `optimizer/setup_check.py`, `settings_catalog.py`, `settings_options.py`, `settings_store.py` | **In-app `/#settings` backend**: 14-probe read-only Setup Check with copy-pasteable `gcloud`/`bq` fixes, 54-setting catalog, live schema/region/teammate dropdown discovery, and append-only `settings_changes` store with 1-click Undo |
| `optimizer/scan_runner.py` | Read-only **Run scan now** engine (`collect` → `rules`, never `execute`) triggered from the Review UI via Cloud Run Job with automatic in-process fallback |
| `optimizer/executor/` | Router + change-window deferral (`OutsideChangeWindow`), per-region Class 1/2/3 applies (`W-01` & `W-02` governance, **Class 3 S0–S9 copy-swap-rebind**), **Class 4 `pr_handoff.py` (`PR_HANDED_OFF`)**, and Recommender write-back |
| `optimizer/verifier.py` | Baseline freeze at approval, Day-14 receipts, regression → rollback path, `rule_accuracy` learning, MV watchdogs, and **Slack/Teams Webhook Alerts** |
| `review_app/` & `frontend/` | React + Flask FinOps Control Plane UI with **Project / Dataset / Region / Owner / Confidence / Min-value filters**, clickable **`RegionsBar` & `RegionBadge`**, **`SettingsPage` (`/#settings`)**, **Ask Gemini Copilot (`gemini-3.8-flash`)**, **PR hand-offs**, OWASP security headers, and `/healthz` |
| `.deploystack/`, `app.json`, `scripts/install.sh`, `docs/INSTALL.md` | **Zero-paste 1-click Cloud Shell installer** (`gcr.io/ds-artifacts-cloudshell/deploystack_custom_image`, interactive `↑`/`↓` project picker, local Docker build acceleration, Direct Cloud Run `--iap`, nightly Cloud Run Job + `03:00 UTC` Scheduler) |
| `docs/OPTIMIZER_OPS_TABLES_SCHEMA_GUIDE.md` & `docs/BIGQUERY_TABLES_CATALOG.md` | Complete schema & data-flow reference for all **18 tables and 16 views** in `optimizer_ops` |
| `docs/HOW_TO_RUN_ON_YOUR_DATASET.md` & `docs/STEP_BY_STEP_OPTIMIZATION_GUIDE.md` | End-to-end operational guides for running against your own datasets and GCP Organization |
| `docs/DEMO_MASTER_PLAYBOOK.md` & `docs/DEMO_MASTER_PLAYBOOK_2.md` (+ `notebooks/`, `scripts/demo_setup*.py`) | Customer Demo Playbooks 1 & 2, BigQuery Studio notebooks, and synthetic fixture generators |
| `terraform/`, `scripts/deploy.sh`, `Dockerfile`, `run.sh` | Production WAF-hardened container (`non-root appuser`, OpenJDK + ZetaSQL `.jar`, Gunicorn) + Terraform IaC with Cloud Armor WAF policy (`sqli`/`xss`) |
| `tests/` | 146 pure-logic unit tests (no live GCP required): `python -m unittest discover -s tests -t .` |

## Quickstart (Local CLI)

```bash
pip install -r requirements.txt
cp config.yaml.example config.yaml   # edit project_id + regions (or deploy in 1 click above)
python -m optimizer.cli init         # creates/updates 18 tables + 16 views in optimizer_ops
python -m optimizer.cli check        # read-only permission & region setup check
python -m optimizer.cli scan         # read-only collect + rules refresh (safe at any hour)
FLASK_APP=review_app.main flask run  # review + approve + edit settings locally
python -m optimizer.cli execute      # applies APPROVED cards inside change_window_utc (defers outside window)
python -m optimizer.cli verify       # after verify_window_days: receipts + regressions + watchdogs
```

## Savings math, FinOps view, money-losing guard & ownership

**1. Savings match how you are billed.**
- On-demand jobs are priced at bytes × \$/TiB; reservation (Editions) jobs at slot-hours ×
  their reservation's edition rate (`v_reservation_rates`, read from `reservation_admin_project`;
  Enterprise list price if unknown). Slot-time savings are scaled by
  `pricing.reservation_savings_realization` (1.0 = autoscale slots you stop paying for; lower it
  when most capacity is committed baseline).
- A job that reads N tables is split proportionally across them, never counted N times.
- **No money-losing cards in the queue:** `min_card_net_monthly_usd` (default `0.0`) automatically
  skips findings — and retires unsigned `PENDING_REVIEW` cards as `SUPERSEDED` — whenever ongoing
  maintenance costs (e.g., MV background refresh) exceed gross savings. \$0 guardrail cards
  (`require_partition_filter`, byte caps) are preserved; operators can inspect hidden negative cards
  anytime via the toolbar's *Incl. money-losing* filter.
- **De-overlapped headline:** cards on the same table / query / dataset compound (50% + 50% = 75%),
  a single-table query rewrite joins its table's cards, Editions and byte-cap cards only count
  against what the table and query fixes leave, and total compute savings are **capped at the last
  30 days' actual compute spend** (`v_jobs_costed`).
- Every card shows its formula ("How this saving is calculated"). Synthetic demo minimums exist
  only with `demo_mode: true` (sandbox only) and are explicitly badged on the card.

**2. Billing controls live in a separate FinOps view.** `W-01`/`W-02`, storage billing model,
reservations/commitments, and project-level cards are shown and approvable only for
`governance.finops_approvers` (plus env `BQOPT_FINOPS_APPROVERS`). Others get HTTP 403
(`FINOPS_PERMISSION_REQUIRED`) and never receive those cards, the `W-01` panel, or the org-wide
spend figure. `execute` re-checks approvers and sends a card back to review if needed. Identity
comes from a verified IAP JWT (`governance.iap_audience`); the UI persona picker is honored only
with `trust_client_identity: true` (demo only).

**3. Service-account spend rolls up to a human owner.** Pick your HR table (`employee_hierarchy_table`)
and service-account mapping table (`service_account_owner_table`) plus column mappings directly from
dropdowns on `<app-url>/#settings` (or in `config.yaml`). PowerBI / Looker / Airflow / dbt
service-account spend then rolls up `SA → human owner → Director` in `v_principal_directory`,
`v_spend_by_director`, and on every card, while `v_unmapped_service_accounts` lists unclaimed
service accounts ranked by 90-day spend.

## Multi-region & cross-project collection (`regions: auto`)

A BigQuery query job runs in a single location, so `sql/02_collector_run.sql` collects the host
project in the **home** location (`location`, where `optimizer_ops` lives). Everything else —
**sibling projects in the home location** plus **all discovered projects in every remote region** —
is collected by `optimizer/remote_collector.py` using the **courier** pattern into the same master
tables (every raw table carries `region` and `project_id`):

1. **Discover (`optimizer/regions.py`):** With `regions: auto`, Cloud Asset Inventory discovers
   every `bigquery.googleapis.com/Dataset` across `asset_scope` (e.g. `organizations/<id>`),
   extracting both BigQuery locations and active `project_id`s (falling back to projects seen in
   `jobs_events` if needed) and upserting `optimizer_ops.regions_seen`. Newly created regions trigger
   a **"New region detected"** alert in the UI's `RegionsBar`.
2. **Watermark & Fetch:** Queries each region's `INFORMATION_SCHEMA` in place (`location=<region>`),
   pulling only rows newer than that region's watermark (180-day backfill on first run; incremental
   thereafter). For table sizes, tries `TABLE_STORAGE_BY_ORGANIZATION` first and automatically falls
   back per project to `TABLE_STORAGE` with fault isolation (a `403` on one project never aborts the rest).
3. **Org-Mode SQL Preview Hydration (`1b_query_previews`):** Because BigQuery omits SQL text from
   `JOBS_BY_ORGANIZATION`, `remote_collector.hydrate_query_previews` automatically queries
   per-project `JOBS_BY_PROJECT` grouped by `(project_id, query_hash)` so clustering filter analysis
   and Class 4 SQL anti-pattern rewrites work across every project in the organization.
4. **Load & Merge:** Uploads rows to short-lived staging tables `optimizer_ops._stg_*` (24-hour
   expiration safety net), `MERGE`s events / refreshes daily snapshots, and drops `_stg_*`.
5. **Least-Privilege Cross-Project Dry-Run (`optimizer/rules.py`):** When verifying Engine 3 Gemini
   SQL rewrites on sibling projects where `bqopt-sa` holds `metadataViewer` without `dataViewer`,
   `_dry_run_sql_with_schema_stubs` compiles the SQL against typed empty schema stubs from
   `INFORMATION_SCHEMA.COLUMNS` so customer row data access is never required.
6. **Regional Execution:** `optimizer/executor/` and `rollback` dispatch every DDL statement in the
   card's `target_region`.

```bash
python -m optimizer.cli regions --no-record     # preview discovered regions & projects without writing
python -m optimizer.cli regions                 # discover + record optimizer_ops.regions_seen
python -m optimizer.cli collect                 # home SQL + sibling projects + every remote region
python -m optimizer.cli scan                    # read-only init/self-heal + collect + rules
```

## Settings page, Setup Check & On-Demand Scan (`/#settings`)

Open the review UI's **Settings** button (or `<app-url>/#settings`):

- **▶ Run scan now**: triggers a strictly read-only `scan` (`collect` → `rules`, never `execute`) via
  the Cloud Run Job (with automatic in-process fallback) and streams live progress in the browser.
- **Setup check**: 14 read-only probes run as the app's service account (query execution,
  `optimizer_ops` read/write, Cloud Asset discovery, per-region `JOBS` / `TABLES` / `TABLE_STORAGE` /
  `RECOMMENDATIONS` / `RESERVATIONS`, cross-project metadata, HR/SA lookup tables, DTS). Any gap
  prints a ready-to-paste `gcloud` / `bq query` command for Cloud Shell.
- **Interactive dropdown editors**: edit everyday settings with live BigQuery table, schema-column,
  region, and teammate-email dropdowns (`optimizer/settings_options.py`). Column-map settings stay
  dimmed ("Not needed — using built-in table") until a custom lookup table is chosen. Every change
  is validated and appended to `optimizer_ops.settings_changes` with who/when/why and 1-click **Undo**,
  and is picked up automatically by both the UI and the nightly Cloud Run Job.

| Role | Set by | Can change |
|---|---|---|
| Settings admin | `governance.settings_admins` in `config.yaml` (or `BQOPT_SETTINGS_ADMINS`), deploy-time only | Everyday settings, verification thresholds, and the settings editors list |
| Settings editor | Added on `/#settings` by an admin (`_access.settings_editors`) | Everyday settings (regions, scope, history windows, lookup tables, Gemini model, Run scan now) |
| + FinOps approver | `governance.finops_approvers` (or `BQOPT_FINOPS_APPROVERS`) | Savings-math settings (minimum card value, realization, caps, risk weights) |

## Honest status matrix

| Component | Status |
|---|---|
| Collector SQL + Multi-Region / Cross-Project Courier | **Complete**: 180-day initial backfill + 3-day incremental `MERGE`, Cloud Asset Inventory discovery (`regions_seen`), cross-project table/column/partition/storage snapshots, and org-mode `1b_query_previews` SQL hydration |
| Schema Self-Heal (`schema_guard.py`) | **Complete**: Stamps `schema_version` on `v_jobs_costed` and non-destructively refreshes DDL whenever views/tables evolve |
| Attribution & Service-Account Rollup | **Complete**: `employee_hierarchy` + `service_account_owners` (`SA → human owner → Director`) powering `v_principal_directory`, `v_unmapped_service_accounts`, `v_spend_by_director`, and `v_director_recommendations` |
| Proactive Cost Guardrail (`W-02`) | **Complete**: Enforces a **50 GiB per-query safety cap (`@@maximum_bytes_billed`) & 50-slot autoscaling sandbox on Human Ad-Hoc Analysts ONLY**, while marking **Service Accounts (`*.iam.gserviceaccount.com`), Airflow, dbt, and Dataform ETL as 100% EXEMPT** |
| Rules + Billing-Aware Scoring + Compiler | **Complete**: 14 infra/billing rules (`W-01`, `W-02`, `C2-01` with `max_staleness = INTERVAL '4' HOUR`), money-losing card suppression (`min_card_net_monthly_usd`), and de-overlapped spend-capped headline |
| Review UI + Filters + Settings (`/#settings`) | **Complete**: Filter by **Project, Dataset, Region (dropdown + clickable `RegionsBar` chips), Owner/Director, Confidence, Min Value, and Class**; live Setup Check, dropdown Settings editors, **Run scan now**, and **Ask Gemini FinOps Copilot (`gemini-3.8-flash`)** |
| Class 1 / 2 / 3 Executor | **Complete**: Regional DDL routing, `W-01` & `W-02` governance applies, Class 2 MV watchdogs, Class 3 S0–S9 copy-swap-rebind, and safe deferral (`OutsideChangeWindow`) when invoked outside `change_window_utc` |
| Class 4 Tri-Engine SQL + PR Hand-Off | **Complete**: **Engine 1** (Regex `C4-01`..`C4-09`), **Engine 2** (Bundled ZetaSQL AST `.jar`), **Engine 3** (Vertex AI **`gemini-3.8-flash`** + cross-project schema-stub `dry_run=True` verifier), and **`pr_handoff.py`** (`APPROVED` → `PR_HANDED_OFF` → **Mark PR merged** → `VERIFYING`) |
| 1-Click Cloud Shell Installer | **Complete**: Zero-paste launch via `deploystack_custom_image`, interactive `↑`/`↓` project picker, `BatchEnable` APIs, local Docker push, Direct Cloud Run `--iap`, and `03:00 UTC` nightly schedule |
| Security & WAF Hardening | **Complete**: Verified IAP JWT auth, ADC client caching, OWASP HTTP headers, `/healthz` readiness probe, non-root Docker user (`UID 10001`), Gunicorn, Slack/Teams webhooks, and Cloud Armor WAF (`terraform/main.tf`) |

## Non-negotiable safety properties (as built)

1. Nothing applies without a human `APPROVED` state; approvals expire (`approval_ttl_days`), and `execute` invoked outside `change_window_utc` safely defers `APPROVED` cards to the next change window rather than failing them.
2. Class 3 captures IAM, row-access policies, labels, and a zero-copy clone **before** touching anything, validates row counts + `FARM_FINGERPRINT` checksums, and aborts cleanly on any pre-swap mismatch.
3. `require_partition_filter` (`C1-02`) re-counts would-break queries **at apply time** and refuses unless zero.
4. Unused-table findings (`C3-05`) carry `ORG_WIDE_READ_CHECK_REQUIRED` when running in single-project `JOBS` scope.
5. Native recommender estimates are capped at the table's real monthly spend × `native_cap_scan_reduction` (default `0.68`), and cards whose recurring cost exceeds gross savings are suppressed (`min_card_net_monthly_usd`).
6. Verification receipts never fabricate: when no post-window executions exist yet, `realized_usd` remains `null` (pending) rather than echoing the prediction.

## Review UI & JSON API

- `/` — React + Vite + Tailwind + ECharts app (`frontend/`, built in Docker multi-stage build and served by Flask).
- `/#settings` — Setup Check, interactive Settings editors, audit history with Undo, and **Run scan now**.
- `/classic` — Server-rendered Jinja UI fallback (including Project, Dataset, Region, and Class filters).
- **JSON API endpoints (`review_app/main.py`):**
  - Dashboard & Actions: `GET /api/dashboard`, `POST /api/decisions` (`approve | reject | reset | unsnooze | rollback | mark_merged`), `POST /api/finops-chat`, `GET /healthz`
  - Settings & Setup Check: `GET /api/setup-check`, `GET /api/settings`, `GET /api/settings/options`, `GET /api/settings/table-columns`, `POST /api/settings`, `POST /api/settings/revert`, `GET /api/settings/history`, `POST /api/settings/editors`
  - On-Demand Scan: `GET /api/scan/status`, `POST /api/scan/run`
- Local dev: `.venv/bin/python3 review_app/main.py --port 8080` then `cd frontend && npm install && npm run dev` (Vite on `:5173` proxies `/api`).
