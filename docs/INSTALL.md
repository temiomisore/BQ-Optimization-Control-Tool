# 1-Click Setup — BigQuery Optimization Control Tool

Welcome! You can deploy the **BigQuery Optimization Control Tool** into your Google Cloud project in **one click** with zero copy-pasting, zero local tools, no YAML editing, and no Load Balancer setup.

---

## Option A · Fully Automatic Launch (Recommended — Zero Pasting)

Click **Open in Cloud Shell** in [`README.md`](../README.md):

1. Check **Trust repo** and click **Confirm** *(click **Authorize** if prompted)*.
2. The installer starts **automatically** and shows an interactive list of your GCP projects — use your **`↑` / `↓` arrow keys + `Enter`** to select the project that will host the web app and `optimizer_ops` dataset.
3. Sit back for ~4 minutes until **`🎉 Setup Complete — Your App Is Live!`** appears with your clickable `<app-url>/#settings` link!

---

## Option B · Run Manually Inside an Existing Cloud Shell Session

If you already have a standard Cloud Shell terminal open:

<walkthrough-project-setup billing="true"></walkthrough-project-setup>

Run:

```bash
./scripts/install.sh --project="<walkthrough-project-id/>" --yes
```

*(Didn't specify `--project`? No problem — the installer automatically lists your Google Cloud projects so you can pick one with `↑`/`↓` or by number `[1]..[N]`!)*

> **Opened a fresh terminal without the repo cloned yet?** Paste this 1-liner to clone and install in one step:
> ```bash
> rm -rf BQ-Optimization-Control-Tool && git clone --depth=1 https://github.com/temiomisore/BQ-Optimization-Control-Tool.git && cd BQ-Optimization-Control-Tool && ./scripts/install.sh --yes
> ```

> **Optional flags** (everything below can also be configured later in the web UI):
> - Separate FinOps approver(s): `--finops="finops-lead@yourcompany.com"`
> - Central slot/reservation admin project: `--reservation-project="your-slot-admin-project"`
> - Start the first BigQuery scan immediately during install: `--run-first-scan`

### What `scripts/install.sh` sets up automatically (~4 minutes) ☕
1. **Your Google Sign-In & Direct IAP:** Makes your signed-in Google account the **Settings Admin** (and initial **FinOps Approver**) and enables **Identity-Aware Proxy (`--iap`)** directly on Cloud Run so colleagues in your corporate domain can sign in securely without configuring a Load Balancer or SSL certificate.
2. **APIs & Least-Privilege Service Account:** Enables BigQuery, Cloud Asset Inventory, Cloud Run, Cloud Scheduler, Recommender, and Vertex AI APIs (`BatchEnable`), creates `bqopt-sa`, and binds org-wide read-only discovery (`roles/cloudasset.viewer`, `roles/bigquery.resourceViewer`, `roles/bigquery.metadataViewer`) when your account has org IAM rights — or starts cleanly in single-project mode and prints the exact 1-line command for your GCP Admin.
3. **Database & Nightly Schedule (`03:00 UTC`):** Initializes all **18 tables and 16 views** in `optimizer_ops`, enables `INFORMATION_SCHEMA.TABLE_STORAGE` across discovered regions, deploys `bqopt-nightly-pipeline`, and schedules `bqopt-nightly-schedule` every night at **`03:00 UTC`** (right inside the `03:00–09:00 UTC` safe change window).

---

## Step 3 · Open Your App & Click "Run scan now"

<walkthrough-conclusion-trophy></walkthrough-conclusion-trophy>

1. Click the **`https://bqopt-review-ui-...run.app/#settings`** link printed at the end of the installer output.
2. Glance at **Setup check** — every working permission shows a green checkmark (and any optional cross-project/org grant shows a ready-to-paste Cloud Shell command).
3. Use the **✏️ pencil icons** to add teammates under **Who can edit settings**, or pick your HR hierarchy & service-account owner tables and columns from dropdowns.
4. Click the blue **▶ Run scan now** button at the top of the page — your recommendations will populate right in the browser, filterable by **Project**, **Dataset**, **Region**, **Owner**, and **Class**!
