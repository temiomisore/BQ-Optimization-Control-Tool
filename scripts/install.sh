#!/usr/bin/env bash
# ==============================================================================
# 1-Click Automated Installer — BigQuery Optimization Control Plane
# ==============================================================================
# Designed for Google Cloud Shell (zero local installs required).
#
# What this script sets up automatically:
#   1. Auto-detects your Google login, GCP Project, Project Number & Organization
#   2. Enables required Google Cloud APIs
#   3. Creates a dedicated Service Account (bqopt-sa) & grants BigQuery / AI roles
#   4. Writes config.yaml with safe production guardrails & makes YOU Settings Admin
#   5. Deploys the Web UI to Cloud Run protected by Direct Identity-Aware Proxy (IAP)
#   6. Deploys the Nightly Pipeline Cloud Run Job + initializes optimizer_ops in BigQuery
#   7. Schedules automatic nightly scans at 02:00 UTC via Cloud Scheduler
#
# Usage:
#   ./scripts/install.sh --yes
#   ./scripts/install.sh --project=my-gcp-project --finops=finops@company.com --yes
# ==============================================================================

set -euo pipefail

# Always run from repository root regardless of caller's working directory.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export CLOUDSDK_CORE_DISABLE_PROMPTS=1

# ---- Defaults & CLI flags ----------------------------------------------------
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
RUN_REGION="${CLOUD_RUN_REGION:-us-central1}"
BQ_LOCATION="US"
OPS_DATASET="optimizer_ops"
ADMIN_EMAIL=""
FINOPS_EMAILS=""
RESERVATION_PROJECT=""
SERVICE_NAME="bqopt-review-ui"
JOB_NAME="bqopt-nightly-pipeline"
SCHEDULER_NAME="bqopt-nightly-schedule"
SA_NAME="bqopt-sa"
ALLOW_DOMAIN="auto"       # auto = allow corporate @domain.com on IAP, skip consumer domains
ENABLE_IAP="true"
ASSUME_YES="false"
RUN_FIRST_SCAN="false"

usage() {
  cat <<'EOF'
Usage: ./scripts/install.sh [options]

Options:
  --project=PROJECT_ID             GCP project to host the app & optimizer_ops dataset
  --region=REGION                  Cloud Run & Scheduler region (default: us-central1)
  --bq-location=LOCATION           BigQuery home location for optimizer_ops (default: US)
  --ops-dataset=DATASET            BigQuery ops dataset name (default: optimizer_ops)
  --admin=EMAIL                    Settings Admin email (default: active gcloud account)
  --finops=EMAIL[,EMAIL...]        FinOps approver email(s) (default: Settings Admin email)
  --reservation-project=PROJECT    Optional BigQuery reservation admin project ID
  --allow-domain / --no-allow-domain
                                   Let colleagues in your corporate email domain sign into the UI
  --run-first-scan                 Kick off the first BigQuery scan automatically after deploy
  -y, --yes                        Non-interactive 1-click mode (accept auto-detected defaults)
  -h, --help                       Show this help
EOF
}

for arg in "$@"; do
  case "$arg" in
    --project=*)             PROJECT_ID="${arg#*=}" ;;
    --region=*)              RUN_REGION="${arg#*=}" ;;
    --bq-location=*)         BQ_LOCATION="${arg#*=}" ;;
    --ops-dataset=*)         OPS_DATASET="${arg#*=}" ;;
    --admin=*)               ADMIN_EMAIL="${arg#*=}" ;;
    --finops=*)              FINOPS_EMAILS="${arg#*=}" ;;
    --reservation-project=*) RESERVATION_PROJECT="${arg#*=}" ;;
    --service-name=*)        SERVICE_NAME="${arg#*=}" ;;
    --job-name=*)            JOB_NAME="${arg#*=}" ;;
    --scheduler-name=*)      SCHEDULER_NAME="${arg#*=}" ;;
    --allow-domain)          ALLOW_DOMAIN="true" ;;
    --no-allow-domain)       ALLOW_DOMAIN="false" ;;
    --no-iap)                ENABLE_IAP="false" ;;
    --run-first-scan)        RUN_FIRST_SCAN="true" ;;
    -y|--yes)                ASSUME_YES="true" ;;
    -h|--help)               usage; exit 0 ;;
    *)
      echo "Unknown option: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done

banner() {
  printf "\n\033[1;36m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n"
  printf "\033[1;37m  %s\033[0m\n" "$1"
  printf "\033[1;36m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n"
}

step() {
  printf "\n\033[1;34m▶ %s\033[0m\n" "$1"
}

ok() {
  printf "  \033[1;32m✓\033[0m %s\n" "$1"
}

warn() {
  printf "  \033[1;33m!\033[0m %s\n" "$1"
}

is_consumer_domain() {
  local d
  d="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$d" in
    gmail.com|googlemail.com|yahoo.com|hotmail.com|outlook.com|live.com|icloud.com|aol.com|proton.me|protonmail.com)
      return 0 ;;
    *)
      return 1 ;;
  esac
}

banner "BigQuery Optimization Control Tool — 1-Click Automated Setup"

# ---- Step 1: Detect identity, project, number & organization -----------------
step "[1/7] Detecting your Google Cloud account & project..."

if [[ -z "$ADMIN_EMAIL" ]]; then
  ADMIN_EMAIL="$(gcloud config get-value account 2>/dev/null | tr -d '[:space:]' || true)"
fi
if [[ -z "$ADMIN_EMAIL" || "$ADMIN_EMAIL" != *"@"* ]]; then
  echo "❌ No active Google Cloud login detected. Run: gcloud auth login" >&2
  exit 1
fi

if [[ -z "$PROJECT_ID" || "$PROJECT_ID" == "(unset)" || "$PROJECT_ID" == "<walkthrough-project-id/>" ]]; then
  PROJECT_ID=""
  _ACTIVE_PROJ="$(gcloud config get-value project 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -n "$_ACTIVE_PROJ" && "$_ACTIVE_PROJ" != "(unset)" ]]; then
    PROJECT_ID="$_ACTIVE_PROJ"
  fi
fi
if [[ -z "$PROJECT_ID" && -t 0 ]]; then
  echo ""
  echo "  Fetching your Google Cloud projects..."
  _PROJ_IDS=()
  _PROJ_NAMES=()
  while IFS=$'\t' read -r _pid _pname; do
    [[ -z "$_pid" ]] && continue
    _PROJ_IDS+=("$_pid")
    _PROJ_NAMES+=("${_pname:-$_pid}")
  done < <(gcloud projects list --sort-by=projectId --limit=30 --format="value(projectId,name)" --quiet 2>/dev/null || true)

  if [[ "${#_PROJ_IDS[@]}" -gt 0 ]]; then
    echo "  Which Google Cloud project should host the app & ${OPS_DATASET} dataset?"
    echo ""
    for _i in "${!_PROJ_IDS[@]}"; do
      printf "    [%d] %-34s (%s)\n" "$((_i + 1))" "${_PROJ_IDS[$_i]}" "${_PROJ_NAMES[$_i]}"
    done
    echo ""
    read -r -p "  Enter a number [1-${#_PROJ_IDS[@]}] or type a Project ID: " _CHOICE
    _CHOICE="$(printf '%s' "$_CHOICE" | tr -d '[:space:]')"
    if [[ "$_CHOICE" =~ ^[0-9]+$ ]] && (( _CHOICE >= 1 && _CHOICE <= ${#_PROJ_IDS[@]} )); then
      PROJECT_ID="${_PROJ_IDS[$((_CHOICE - 1))]}"
    else
      PROJECT_ID="$_CHOICE"
    fi
  else
    read -r -p "  Enter the GCP Project ID to install into: " PROJECT_ID
    PROJECT_ID="$(printf '%s' "$PROJECT_ID" | tr -d '[:space:]')"
  fi
fi
if [[ -z "$PROJECT_ID" ]]; then
  echo "❌ No GCP Project selected. Pick a project at the top of Cloud Shell or pass --project=YOUR_PROJECT_ID" >&2
  exit 1
fi

gcloud config set project "$PROJECT_ID" --quiet >/dev/null 2>&1 || true

if [[ -z "$FINOPS_EMAILS" ]]; then
  FINOPS_EMAILS="$ADMIN_EMAIL"
fi

EMAIL_DOMAIN="${ADMIN_EMAIL#*@}"
SHARE_WITH_DOMAIN="false"
if [[ "$ALLOW_DOMAIN" == "true" ]]; then
  if ! is_consumer_domain "$EMAIL_DOMAIN"; then
    SHARE_WITH_DOMAIN="true"
  fi
elif [[ "$ALLOW_DOMAIN" == "auto" ]]; then
  if ! is_consumer_domain "$EMAIL_DOMAIN"; then
    SHARE_WITH_DOMAIN="true"
  fi
fi

ok "Signed in as        : ${ADMIN_EMAIL} (Settings Admin)"
ok "Target GCP Project  : ${PROJECT_ID}"
ok "BigQuery Location   : ${BQ_LOCATION} (dataset: ${OPS_DATASET})"
ok "Cloud Run Region    : ${RUN_REGION}"
ok "FinOps Approver(s)  : ${FINOPS_EMAILS}"
if [[ "$ENABLE_IAP" == "true" && "$SHARE_WITH_DOMAIN" == "true" ]]; then
  ok "Sign-in Access (IAP): @${EMAIL_DOMAIN} teammates (viewers by default; only you are Admin)"
fi

if [[ "$ASSUME_YES" == "false" && -t 0 ]]; then
  echo ""
  read -r -p "  Press Enter to set everything up automatically (or Ctrl+C to cancel)... " _
fi

# Enable APIs first so project number, ancestry, Cloud Run, Build, IAP & Scheduler work cleanly.
step "[2/7] Enabling required Google Cloud APIs (~30-60s on a fresh project)..."
gcloud services enable \
  cloudresourcemanager.googleapis.com \
  serviceusage.googleapis.com \
  iam.googleapis.com \
  bigquery.googleapis.com \
  bigquerydatatransfer.googleapis.com \
  bigquerystorage.googleapis.com \
  bigqueryreservation.googleapis.com \
  recommender.googleapis.com \
  cloudasset.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com \
  iap.googleapis.com \
  aiplatform.googleapis.com \
  --project="$PROJECT_ID" --quiet
ok "All required Google Cloud APIs enabled."

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" --quiet)"
ANCESTORS_JSON="$(gcloud projects get-ancestors "$PROJECT_ID" --format=json --quiet 2>/dev/null || echo "[]")"
ORG_ID="$(ANCESTORS_JSON="$ANCESTORS_JSON" python3 - <<'PY'
import json, os
try:
    rows = json.loads(os.environ.get("ANCESTORS_JSON", "[]"))
    for r in rows:
        if r.get("type") == "organization" and r.get("id"):
            print(r["id"])
            break
except Exception:
    pass
PY
)"

if [[ -n "$ORG_ID" ]]; then
  ok "Detected GCP Organization: organizations/${ORG_ID}"
else
  warn "No parent GCP Organization detected — scoping discovery to project ${PROJECT_ID}."
fi

# ---- Step 3: Service Account & IAM -------------------------------------------
step "[3/7] Creating Service Account & granting permissions..."
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" \
    --project="$PROJECT_ID" \
    --display-name="BigQuery Optimization Control Plane" \
    --description="Runs the BQ Optimization Review UI and nightly telemetry/rules pipeline" \
    --quiet
  ok "Created service account ${SA_EMAIL} (waiting 10s for IAM propagation)..."
  sleep 10
else
  ok "Service account ${SA_EMAIL} already exists."
fi

PROJECT_ROLES=(
  "roles/bigquery.jobUser"
  "roles/bigquery.dataEditor"
  "roles/bigquery.metadataViewer"
  "roles/bigquery.resourceViewer"
  "roles/recommender.bigqueryPartitionClusterViewer"
  "roles/aiplatform.user"
)
for ROLE in "${PROJECT_ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$ROLE" \
    --condition=None \
    --quiet >/dev/null
done
ok "Granted project-level BigQuery, Recommender & Vertex AI roles on ${PROJECT_ID}."

# Attempt org-wide read-only discovery & JOBS_BY_ORGANIZATION grants if in an org.
ORG_IAM_READY="false"
MISSING_ORG_CMD=""
if [[ -n "$ORG_ID" ]]; then
  if gcloud organizations add-iam-policy-binding "$ORG_ID" \
       --member="serviceAccount:${SA_EMAIL}" \
       --role="roles/cloudasset.viewer" \
       --condition=None --quiet >/dev/null 2>&1 \
     && gcloud organizations add-iam-policy-binding "$ORG_ID" \
       --member="serviceAccount:${SA_EMAIL}" \
       --role="roles/bigquery.resourceViewer" \
       --condition=None --quiet >/dev/null 2>&1; then
    # Optional org-level metadataViewer for cross-project storage summary (best-effort)
    gcloud organizations add-iam-policy-binding "$ORG_ID" \
      --member="serviceAccount:${SA_EMAIL}" \
      --role="roles/bigquery.metadataViewer" \
      --condition=None --quiet >/dev/null 2>&1 || true
    ORG_IAM_READY="true"
    ok "Granted org-wide read-only discovery (cloudasset.viewer + bigquery.resourceViewer)."
  else
    warn "Your account doesn't manage Organization-level IAM — starting safely in single-project mode."
    warn "Everything will still deploy cleanly! We'll print 1 command at the end for your GCP Admin if you want org-wide scanning."
    MISSING_ORG_CMD="for ROLE in roles/cloudasset.viewer roles/bigquery.resourceViewer roles/bigquery.metadataViewer; do gcloud organizations add-iam-policy-binding ${ORG_ID} --member=\"serviceAccount:${SA_EMAIL}\" --role=\"\$ROLE\" --condition=None; done"
  fi
fi

# Optional reservation admin project read access
MISSING_RES_CMD=""
if [[ -n "$RESERVATION_PROJECT" && "$RESERVATION_PROJECT" != "$PROJECT_ID" ]]; then
  if gcloud projects add-iam-policy-binding "$RESERVATION_PROJECT" \
       --member="serviceAccount:${SA_EMAIL}" \
       --role="roles/bigquery.resourceViewer" \
       --condition=None --quiet >/dev/null 2>&1; then
    ok "Granted reservation read access on ${RESERVATION_PROJECT}."
  else
    warn "Couldn't grant reservation viewer on ${RESERVATION_PROJECT} automatically (will show command at end)."
    MISSING_RES_CMD="gcloud projects add-iam-policy-binding ${RESERVATION_PROJECT} --member=\"serviceAccount:${SA_EMAIL}\" --role=\"roles/bigquery.resourceViewer\" --condition=None"
  fi
fi

# ---- Step 4: Generate production config.yaml ---------------------------------
step "[4/7] Generating config.yaml..."

IAP_AUDIENCE=""
if [[ "$ENABLE_IAP" == "true" ]]; then
  IAP_AUDIENCE="/projects/${PROJECT_NUMBER}/locations/${RUN_REGION}/services/${SERVICE_NAME}"
fi

ASSET_SCOPE="projects/${PROJECT_ID}"
JOBS_VIEW="JOBS"
if [[ -n "$ORG_ID" ]]; then
  ASSET_SCOPE="organizations/${ORG_ID}"
  if [[ "$ORG_IAM_READY" == "true" ]]; then
    JOBS_VIEW="JOBS_BY_ORGANIZATION"
  fi
fi

write_config_yaml() {
  local _iap_aud="$1"
  PROJECT_ID="$PROJECT_ID" \
  OPS_DATASET="$OPS_DATASET" \
  BQ_LOCATION="$BQ_LOCATION" \
  ASSET_SCOPE="$ASSET_SCOPE" \
  JOBS_VIEW="$JOBS_VIEW" \
  RESERVATION_PROJECT="$RESERVATION_PROJECT" \
  ADMIN_EMAIL="$ADMIN_EMAIL" \
  FINOPS_EMAILS="$FINOPS_EMAILS" \
  IAP_AUDIENCE="$_iap_aud" \
  python3 - <<'PY'
import os

proj = os.environ["PROJECT_ID"]
ops_ds = os.environ["OPS_DATASET"]
loc = os.environ["BQ_LOCATION"]
scope = os.environ["ASSET_SCOPE"]
jobs_view = os.environ["JOBS_VIEW"]
res_proj = os.environ.get("RESERVATION_PROJECT", "").strip()
admin = os.environ["ADMIN_EMAIL"].strip().lower()
finops = [e.strip().lower() for e in os.environ.get("FINOPS_EMAILS", "").split(",") if e.strip()]
iap_aud = os.environ.get("IAP_AUDIENCE", "").strip()

res_yaml = f'"{res_proj}"' if res_proj else "null"
iap_yaml = f'"{iap_aud}"' if iap_aud else "null"
finops_lines = "\n".join(f"    - {e}" for e in dict.fromkeys(finops)) if finops else "    []"

content = f"""# Generated by scripts/install.sh — day-to-day settings can be edited in the UI (<app-url>/#settings).
project_id: "{proj}"
ops_dataset: "{ops_ds}"
location: "{loc}"

regions: auto
asset_scope: "{scope}"
jobs_view: "{jobs_view}"
reservation_admin_project: {res_yaml}
employee_hierarchy_table: null
service_account_owner_table: null
min_card_net_monthly_usd: 0

governance:
  settings_admins:
    - {admin}
  finops_approvers:
{finops_lines}
  trust_client_identity: false
  iap_audience: {iap_yaml}

executor:
  enable_class1: true
  enable_class2: false
  enable_class3: false
  enable_reservation_changes: false
  change_window_utc: [3, 9]

verify_window_days: 14
demo_mode: false
"""
with open("config.yaml", "w", encoding="utf-8") as f:
    f.write(content)
PY
}

write_config_yaml "$IAP_AUDIENCE"
ok "Wrote config.yaml (Admin: ${ADMIN_EMAIL}, Scope: ${ASSET_SCOPE}, View: ${JOBS_VIEW})."

# ---- Step 5: Deploy Cloud Run Web UI (+ Direct IAP) --------------------------
step "[5/7] Building & deploying the Web UI to Cloud Run (~2-3 minutes)..."

ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},BQOPT_PIPELINE_JOB=${JOB_NAME},BQOPT_JOB_REGION=${RUN_REGION}"
if [[ -n "$IAP_AUDIENCE" ]]; then
  ENV_VARS="${ENV_VARS},IAP_AUDIENCE=${IAP_AUDIENCE}"
fi

DEPLOY_IAP_ACTIVE="false"
if [[ "$ENABLE_IAP" == "true" ]]; then
  # Provision the Google-managed IAP Service Agent so IAP can invoke the private Cloud Run service.
  gcloud beta services identity create --service=iap.googleapis.com --project="$PROJECT_ID" --quiet >/dev/null 2>&1 || true

  if gcloud run deploy "$SERVICE_NAME" \
       --project="$PROJECT_ID" \
       --region="$RUN_REGION" \
       --source=. \
       --service-account="$SA_EMAIL" \
       --memory=1Gi \
       --cpu=1 \
       --timeout=900 \
       --no-allow-unauthenticated \
       --iap \
       --set-env-vars="$ENV_VARS" \
       --quiet; then
    DEPLOY_IAP_ACTIVE="true"
  else
    warn "Direct --iap flag was rejected by project/org policy; redeploying private service without --iap flag..."
    IAP_AUDIENCE=""
    write_config_yaml ""
    gcloud run deploy "$SERVICE_NAME" \
      --project="$PROJECT_ID" \
      --region="$RUN_REGION" \
      --source=. \
      --service-account="$SA_EMAIL" \
      --memory=1Gi \
      --cpu=1 \
      --timeout=900 \
      --no-allow-unauthenticated \
      --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},BQOPT_PIPELINE_JOB=${JOB_NAME},BQOPT_JOB_REGION=${RUN_REGION}" \
      --quiet
  fi
else
  gcloud run deploy "$SERVICE_NAME" \
    --project="$PROJECT_ID" \
    --region="$RUN_REGION" \
    --source=. \
    --service-account="$SA_EMAIL" \
    --memory=1Gi \
    --cpu=1 \
    --timeout=900 \
    --no-allow-unauthenticated \
    --set-env-vars="$ENV_VARS" \
    --quiet
fi

if [[ "$DEPLOY_IAP_ACTIVE" == "true" ]]; then
  IAP_SA="service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com"
  gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
    --project="$PROJECT_ID" \
    --region="$RUN_REGION" \
    --member="serviceAccount:${IAP_SA}" \
    --role="roles/run.invoker" \
    --quiet >/dev/null

  gcloud iap web add-iam-policy-binding \
    --project="$PROJECT_ID" \
    --resource-type=cloud-run \
    --region="$RUN_REGION" \
    --service="$SERVICE_NAME" \
    --member="user:${ADMIN_EMAIL}" \
    --role="roles/iap.httpsResourceAccessor" \
    --quiet >/dev/null

  IFS=',' read -r -a _FIN_ARR <<< "$FINOPS_EMAILS"
  for _FE in "${_FIN_ARR[@]}"; do
    _FE="$(echo "$_FE" | tr -d '[:space:]')"
    if [[ -n "$_FE" && "$_FE" != "$ADMIN_EMAIL" ]]; then
      gcloud iap web add-iam-policy-binding \
        --project="$PROJECT_ID" \
        --resource-type=cloud-run \
        --region="$RUN_REGION" \
        --service="$SERVICE_NAME" \
        --member="user:${_FE}" \
        --role="roles/iap.httpsResourceAccessor" \
        --quiet >/dev/null || true
    fi
  done

  if [[ "$SHARE_WITH_DOMAIN" == "true" ]]; then
    gcloud iap web add-iam-policy-binding \
      --project="$PROJECT_ID" \
      --resource-type=cloud-run \
      --region="$RUN_REGION" \
      --service="$SERVICE_NAME" \
      --member="domain:${EMAIL_DOMAIN}" \
      --role="roles/iap.httpsResourceAccessor" \
      --quiet >/dev/null || true
  fi
  ok "Secured ${SERVICE_NAME} behind Google Identity-Aware Proxy (no Load Balancer needed)."
fi

SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" --project="$PROJECT_ID" --region="$RUN_REGION" --format="value(status.url)" --quiet)"
IMAGE_URI="$(gcloud run services describe "$SERVICE_NAME" --project="$PROJECT_ID" --region="$RUN_REGION" --format="value(template.containers[0].image)" --quiet)"
ok "Web UI deployed at: ${SERVICE_URL}"

# ---- Step 6: Nightly Cloud Run Job + BigQuery Schema Init --------------------
step "[6/7] Creating Nightly Pipeline Job & initializing BigQuery dataset (${OPS_DATASET})..."

gcloud run jobs deploy "$JOB_NAME" \
  --project="$PROJECT_ID" \
  --region="$RUN_REGION" \
  --image="$IMAGE_URI" \
  --service-account="$SA_EMAIL" \
  --memory=2Gi \
  --cpu=2 \
  --task-timeout=3600s \
  --max-retries=0 \
  --command=python \
  --args="-m,optimizer.cli,pipeline" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
  --quiet

# Let the Web UI's "Run scan now" button & Cloud Scheduler invoke this job cleanly.
gcloud run jobs add-iam-policy-binding "$JOB_NAME" \
  --project="$PROJECT_ID" \
  --region="$RUN_REGION" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.developer" \
  --quiet >/dev/null

ok "Deployed Cloud Run Job ${JOB_NAME} and linked 'Run scan now' button."

gcloud run jobs execute "$JOB_NAME" \
  --project="$PROJECT_ID" \
  --region="$RUN_REGION" \
  --args="-m,optimizer.cli,init" \
  --wait \
  --quiet
ok "Initialized BigQuery dataset ${PROJECT_ID}.${OPS_DATASET} (tables + views ready)."

# ---- Step 7: Nightly Cloud Scheduler (02:00 UTC) -----------------------------
step "[7/7] Scheduling automatic nightly scans (02:00 UTC)..."

SCHEDULER_URI="https://${RUN_REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"
if gcloud scheduler jobs describe "$SCHEDULER_NAME" --project="$PROJECT_ID" --location="$RUN_REGION" --quiet >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SCHEDULER_NAME" \
    --project="$PROJECT_ID" \
    --location="$RUN_REGION" \
    --schedule="0 2 * * *" \
    --time-zone="Etc/UTC" \
    --uri="$SCHEDULER_URI" \
    --http-method=POST \
    --oauth-service-account-email="$SA_EMAIL" \
    --quiet >/dev/null
else
  gcloud scheduler jobs create http "$SCHEDULER_NAME" \
    --project="$PROJECT_ID" \
    --location="$RUN_REGION" \
    --schedule="0 2 * * *" \
    --time-zone="Etc/UTC" \
    --uri="$SCHEDULER_URI" \
    --http-method=POST \
    --oauth-service-account-email="$SA_EMAIL" \
    --description="Nightly BigQuery Optimization Control Plane run (collect -> rules -> execute approved -> verify)" \
    --quiet >/dev/null
fi
ok "Scheduled ${SCHEDULER_NAME} every night at 02:00 UTC."

if [[ "$RUN_FIRST_SCAN" == "true" ]]; then
  step "Starting your first BigQuery scan in the background..."
  gcloud run jobs execute "$JOB_NAME" \
    --project="$PROJECT_ID" \
    --region="$RUN_REGION" \
    --args="-m,optimizer.cli,scan" \
    --async \
    --quiet
  ok "First scan is running in the background!"
fi

banner "🎉 Setup Complete — Your App Is Live!"
echo ""
echo "  👉 Click here to open Settings & run your first scan:"
echo "     ${SERVICE_URL}/#settings"
echo ""
echo "  What to do next (all inside the browser — no terminal needed):"
echo "    1. Check 'Setup check' on that page to confirm green checkmarks."
echo "    2. Optionally add teammates under 'Who can edit settings' or pick your HR / owner tables."
echo "    3. Click the blue 'Run scan now' button at the top of the page!"
echo ""

if [[ -n "$MISSING_ORG_CMD" ]]; then
  echo "  ── Optional: Enable Organization-Wide Scanning ──────────────────────────"
  echo "  Ask a GCP Organization Admin to run this once, then switch 'Query history"
  echo "  source' to 'Entire organization' in Settings:"
  echo ""
  echo "    ${MISSING_ORG_CMD}"
  echo ""
fi

if [[ -n "$MISSING_RES_CMD" ]]; then
  echo "  ── Optional: Enable Reservation Admin Telemetry ─────────────────────────"
  echo "  Ask an owner of project '${RESERVATION_PROJECT}' to run this once:"
  echo ""
  echo "    ${MISSING_RES_CMD}"
  echo ""
fi
