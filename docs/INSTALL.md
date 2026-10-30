# 1-Click Setup — BigQuery Optimization Control Tool

Welcome! This guided setup deploys the **BigQuery Optimization Control Tool** into your Google Cloud project in **two clicks** — no local tools, no YAML editing, and no Load Balancer setup.

---

## Step 1 · Pick Your Google Cloud Project

Choose the Google Cloud project that will host the web app and the `optimizer_ops` BigQuery dataset:

<walkthrough-project-setup billing="true"></walkthrough-project-setup>

Once a project is selected above, click **Next** below.

---

## Step 2 · Run the Automated Installer (~4 Minutes)

Click the **Copy to Cloud Shell** button on the command below and press **Enter** in the terminal at the bottom of your screen:

```bash
./scripts/install.sh --project="<walkthrough-project-id/>" --yes
```

*(Didn't pick a project in Step 1 yet? No problem — the installer automatically lists your Google Cloud projects as `[1]`, `[2]`, `[3]` so you can pick one by number!)*

> **Switched Google accounts or opened a fresh Cloud Shell tab?** If your terminal says `No such file or directory`, paste this 1-liner instead to clone & run in one step:
> ```bash
> rm -rf BQ-Optimization-Control-Tool && git clone --depth=1 https://github.com/temiomisore/BQ-Optimization-Control-Tool.git && cd BQ-Optimization-Control-Tool && ./scripts/install.sh --yes
> ```

> **Optional flags** (only if you already know them — otherwise you can set everything in the web UI afterward!):
> - Have a separate FinOps approver? Add `--finops="finops-lead@yourcompany.com"`
> - Have a central slot/reservation admin project? Add `--reservation-project="your-slot-admin-project"`

### What this sets up automatically while you grab coffee ☕
1. **Your Google Sign-In:** Makes your logged-in Google account the **Settings Admin** and turns on **Identity-Aware Proxy (IAP)** directly on Cloud Run (so teammates in your company domain can view recommendations securely without setting up a Load Balancer or SSL certs).
2. **APIs & Service Account:** Enables BigQuery, Cloud Asset, Cloud Run, Cloud Scheduler, and Vertex AI APIs and creates a least-privilege `bqopt-sa` service account.
3. **Database & Nightly Schedule:** Initializes the `optimizer_ops` BigQuery dataset and schedules automatic nightly refreshes at **02:00 UTC**.

When you see **`🎉 Setup Complete — Your App Is Live!`**, click **Next**.

---

## Step 3 · Open Your App & Click "Run scan now"

<walkthrough-conclusion-trophy></walkthrough-conclusion-trophy>

1. Click the **`https://bqopt-review-ui-...run.app/#settings`** link printed at the end of the installer output in your terminal.
2. Glance at **Setup check** — every working permission shows a green checkmark (and if your IT team manages Organization-level permissions, it shows the exact 1-line command to send them).
3. Use the **✏️ pencil icons** to add teammates under **Who can edit settings**, or pick your HR hierarchy & service-account owner tables from dropdowns.
4. Click the blue **▶ Run scan now** button right at the top of the page — your recommendations will appear automatically in the browser!
