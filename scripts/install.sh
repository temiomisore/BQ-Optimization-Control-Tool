#!/usr/bin/env bash
# ==============================================================================
# 1-Click Automated Installer — BigQuery Optimization Control Plane
# ==============================================================================
# Designed for Google Cloud Shell (zero local installs or copy-pasting required).
#
# Supports ALL THREE launch modes:
#   - Automatic zero-paste launch via Google Cloud Shell's certified, pre-cached
#     `gcr.io/ds-artifacts-cloudshell/deploystack_custom_image` (`.deploystack/scripts/preinstall.sh`)
#   - `app.json` lifecycle hooks (`--phase=prepare|postbuild|precreate|finalize`)
#   - Standalone manual CLI execution (`./scripts/install.sh --yes`)
#
# What this script sets up automatically:
#   1. Auto-detects your Google login, interactively asks which GCP Project to deploy into
#      (when `--prompt-project` or unset), and reads Project Number & Organization
#   2. Enables required Google Cloud APIs via `BatchEnable` (zero `Services.List` quota calls)
#   3. Creates a dedicated Service Account (bqopt-sa) & grants BigQuery / AI roles
#   4. Writes config.yaml with safe production guardrails & makes YOU Settings Admin
#   5. Deploys the Web UI to Cloud Run protected by Direct Identity-Aware Proxy (IAP)
#   6. Deploys the Nightly Pipeline Cloud Run Job + initializes optimizer_ops in BigQuery
#   7. Schedules automatic nightly scans at 02:00 UTC via Cloud Scheduler
#
# Usage:
#   ./scripts/install.sh --prompt-project --yes
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
RUN_REGION="${CLOUD_RUN_REGION:-${GOOGLE_CLOUD_REGION:-us-central1}}"
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
PROMPT_PROJECT="false"
RUN_FIRST_SCAN="false"
PHASE="all"               # all | prepare | postbuild | precreate | finalize
PREBUILT_IMAGE="${IMAGE_URL:-}"
EXPLICIT_PROJECT="false"
EXPLICIT_REGION="false"
STATE_FILE="${REPO_ROOT}/.bqopt_prepared.env"

usage() {
  cat <<'EOF'
Usage: ./scripts/install.sh [options]

Options:
  --project=PROJECT_ID             GCP project to host the app & optimizer_ops dataset
  --prompt-project                 Always ask interactively which GCP project to deploy into
  --region=REGION                  Cloud Run & Scheduler region (default: us-central1)
  --bq-location=LOCATION           BigQuery home location for optimizer_ops (default: US)
  --ops-dataset=DATASET            BigQuery ops dataset name (default: optimizer_ops)
  --admin=EMAIL                    Settings Admin email (default: active gcloud account)
  --finops=EMAIL[,EMAIL...]        FinOps approver email(s) (default: Settings Admin email)
  --reservation-project=PROJECT    Optional BigQuery reservation admin project ID
  --allow-domain / --no-allow-domain
                                   Let colleagues in your corporate email domain sign into the UI
  --run-first-scan                 Kick off the first BigQuery scan automatically after deploy
  --image=IMAGE_URI                Deploy from a pre-built container image instead of source build
  --phase=PHASE                    Lifecycle phase: all (default), prepare, postbuild, precreate, finalize
  -y, --yes                        Non-interactive 1-click mode (accept auto-detected defaults)
  -h, --help                       Show this help
EOF
}

for arg in "$@"; do
  case "$arg" in
    --project=*)             PROJECT_ID="${arg#*=}"; EXPLICIT_PROJECT="true" ;;
    --prompt-project)        PROMPT_PROJECT="true" ;;
    --region=*)              RUN_REGION="${arg#*=}"; EXPLICIT_REGION="true" ;;
    --bq-location=*)         BQ_LOCATION="${arg#*=}" ;;
    --ops-dataset=*)         OPS_DATASET="${arg#*=}" ;;
    --admin=*)               ADMIN_EMAIL="${arg#*=}" ;;
    --finops=*)              FINOPS_EMAILS="${arg#*=}" ;;
    --reservation-project=*) RESERVATION_PROJECT="${arg#*=}" ;;
    --service-name=*)        SERVICE_NAME="${arg#*=}" ;;
    --job-name=*)            JOB_NAME="${arg#*=}" ;;
    --scheduler-name=*)      SCHEDULER_NAME="${arg#*=}" ;;
    --image=*)               PREBUILT_IMAGE="${arg#*=}" ;;
    --phase=*)               PHASE="${arg#*=}" ;;
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

case "$PHASE" in
  all|prepare|postbuild|precreate|finalize) ;;
  *)
    echo "Unknown --phase value: $PHASE (expected all|prepare|postbuild|precreate|finalize)" >&2
    exit 1
    ;;
esac

# Guard against upstream cloud-run-button's existingEnvVars formatting (`GOOGLE_CLOUD_PROJECT={}`)
# and reliably extract region + project from `$IMAGE_URL` (`<region>-docker.pkg.dev/<project>/...`).
if [[ "$PROJECT_ID" == "{}" ]]; then
  PROJECT_ID=""
fi
if [[ "$RUN_REGION" == "{}" || -z "$RUN_REGION" ]]; then
  RUN_REGION="us-central1"
fi
if [[ -n "$PREBUILT_IMAGE" && "$PREBUILT_IMAGE" == *-docker.pkg.dev/*/*/* ]]; then
  _IMG_NO_REF="${PREBUILT_IMAGE%%@*}"
  _IMG_NO_REF="${_IMG_NO_REF%%:*}"
  _IMG_HOST="${_IMG_NO_REF%%/*}"
  _IMG_REST="${_IMG_NO_REF#*/}"
  _IMG_REGION="${_IMG_HOST%-docker.pkg.dev}"
  _IMG_PROJECT="${_IMG_REST%%/*}"
  if [[ "$EXPLICIT_PROJECT" == "false" && -n "$_IMG_PROJECT" ]]; then
    PROJECT_ID="$_IMG_PROJECT"
  fi
  if [[ "$EXPLICIT_REGION" == "false" && -n "$_IMG_REGION" && "$_IMG_REGION" != "$_IMG_HOST" ]]; then
    RUN_REGION="$_IMG_REGION"
  fi
fi

# When invoked via `gcr.io/cloudrun/button` hooks, write directly to /dev/tty so banners render
# in full color instead of `scripts.go`'s dim grey wrapper (can be disabled via BQOPT_NO_TTY=1).
if [[ "$PHASE" != "all" && "${BQOPT_NO_TTY:-0}" != "1" && -c /dev/tty ]] && { true >/dev/tty; } 2>/dev/null; then
  exec >/dev/tty 2>/dev/tty
fi

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

save_prepared_state() {
  STATE_FILE="$STATE_FILE" \
  EXISTING_BEFORE="$EXISTING_BEFORE" \
  ADMIN_EMAIL="$ADMIN_EMAIL" \
  PROJECT_ID="$PROJECT_ID" \
  PROJECT_NUMBER="$PROJECT_NUMBER" \
  RUN_REGION="$RUN_REGION" \
  BQ_LOCATION="$BQ_LOCATION" \
  OPS_DATASET="$OPS_DATASET" \
  ORG_ID="$ORG_ID" \
  ORG_IAM_READY="$ORG_IAM_READY" \
  ASSET_SCOPE="$ASSET_SCOPE" \
  JOBS_VIEW="$JOBS_VIEW" \
  RESERVATION_PROJECT="$RESERVATION_PROJECT" \
  FINOPS_EMAILS="$FINOPS_EMAILS" \
  EMAIL_DOMAIN="$EMAIL_DOMAIN" \
  SHARE_WITH_DOMAIN="$SHARE_WITH_DOMAIN" \
  ENABLE_IAP="$ENABLE_IAP" \
  IAP_AUDIENCE="$IAP_AUDIENCE" \
  SERVICE_NAME="$SERVICE_NAME" \
  JOB_NAME="$JOB_NAME" \
  SCHEDULER_NAME="$SCHEDULER_NAME" \
  SA_NAME="$SA_NAME" \
  SA_EMAIL="$SA_EMAIL" \
  MISSING_ORG_CMD="$MISSING_ORG_CMD" \
  MISSING_RES_CMD="$MISSING_RES_CMD" \
  python3 - <<'PY'
import os, shlex

keys = [
    "EXISTING_BEFORE", "ADMIN_EMAIL", "PROJECT_ID", "PROJECT_NUMBER",
    "RUN_REGION", "BQ_LOCATION", "OPS_DATASET", "ORG_ID", "ORG_IAM_READY",
    "ASSET_SCOPE", "JOBS_VIEW", "RESERVATION_PROJECT", "FINOPS_EMAILS",
    "EMAIL_DOMAIN", "SHARE_WITH_DOMAIN", "ENABLE_IAP", "IAP_AUDIENCE",
    "SERVICE_NAME", "JOB_NAME", "SCHEDULER_NAME", "SA_NAME", "SA_EMAIL",
    "MISSING_ORG_CMD", "MISSING_RES_CMD",
]
with open(os.environ["STATE_FILE"], "w", encoding="utf-8") as f:
    for k in keys:
        f.write(f"{k}={shlex.quote(os.environ.get(k, ''))}\n")
PY
}

load_prepared_state() {
  if [[ ! -f "$STATE_FILE" ]]; then
    echo "❌ Setup preparation state (${STATE_FILE}) is missing — an earlier step failed." >&2
    return 1
  fi
  # shellcheck disable=SC1090
  source "$STATE_FILE"
}

run_prepare_steps() {
  rm -f "$STATE_FILE"
  banner "BigQuery Optimization Control Tool — 1-Click Automated Setup"

  # ---- Step 1: Detect identity, project, number & organization ---------------
  step "[1/7] Detecting your Google Cloud account & project..."

  local _has_tty="false"
  if [[ -t 0 ]]; then
    _has_tty="true"
  elif [[ "${BQOPT_NO_TTY:-0}" != "1" && -c /dev/tty ]] && { true </dev/tty; } 2>/dev/null; then
    _has_tty="true"
  fi

  if [[ -z "$ADMIN_EMAIL" ]]; then
    ADMIN_EMAIL="$(gcloud config get-value account 2>/dev/null | tr -d '[:space:]' || true)"
  fi
  if [[ -z "$ADMIN_EMAIL" || "$ADMIN_EMAIL" != *"@"* ]]; then
    if [[ "$_has_tty" == "true" && "${BQOPT_NO_TTY:-0}" != "1" ]]; then
      warn "Cloud Shell opened without your Google login (this happens if 'Trust repo' wasn't checked)."
      warn "Sign in below to continue, OR re-click 'Open in Cloud Shell' and check 'Trust repo':"
      CLOUDSDK_CORE_DISABLE_PROMPTS=0 gcloud auth login </dev/tty >/dev/tty 2>/dev/tty || true
      ADMIN_EMAIL="$(gcloud config get-value account 2>/dev/null | tr -d '[:space:]' || true)"
    fi
  fi
  if [[ -z "$ADMIN_EMAIL" || "$ADMIN_EMAIL" != *"@"* ]]; then
    echo "❌ No active Google Cloud login detected. Run: gcloud auth login" >&2
    exit 1
  fi

  local _active_proj=""
  _active_proj="$(gcloud config get-value project 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ "$_active_proj" == "(unset)" || "$_active_proj" == "{}" || "$_active_proj" == "<walkthrough-project-id/>" ]]; then
    _active_proj=""
  fi

  if [[ "$PROMPT_PROJECT" == "true" && "$EXPLICIT_PROJECT" == "false" ]]; then
    PROJECT_ID=""
  elif [[ -z "$PROJECT_ID" || "$PROJECT_ID" == "(unset)" || "$PROJECT_ID" == "<walkthrough-project-id/>" ]]; then
    PROJECT_ID="$_active_proj"
  fi

  if [[ -z "$PROJECT_ID" && "$_has_tty" == "true" ]]; then
    echo ""
    echo "  Fetching your Google Cloud projects..."
    local _proj_tsv=""
    _proj_tsv="$(gcloud projects list --sort-by=projectId --limit=100 --format="value(projectId,name)" --quiet 2>/dev/null || true)"

    if [[ -n "$_proj_tsv" && "${BQOPT_NO_TTY:-0}" != "1" && "${BQOPT_SIMPLE_PICKER:-0}" != "1" && -c /dev/tty ]] && { true </dev/tty >/dev/tty; } 2>/dev/null; then
      local _pick_rc=0
      PROJECT_ID="$(PROJ_TSV="$_proj_tsv" DEFAULT_PROJ="$_active_proj" OPS_DATASET="$OPS_DATASET" python3 - <<'PY'
import os, select, sys, termios, tty

rows = []
for raw in os.environ.get("PROJ_TSV", "").splitlines():
    if not raw.strip():
        continue
    parts = raw.split("\t", 1)
    pid = parts[0].strip()
    pname = (parts[1].strip() if len(parts) > 1 and parts[1].strip() else pid)
    if pid:
        rows.append((pid, pname))

if not rows:
    sys.exit(2)

default_proj = os.environ.get("DEFAULT_PROJ", "").strip()
ops_ds = os.environ.get("OPS_DATASET", "optimizer_ops")
idx = next((i for i, (pid, _) in enumerate(rows) if pid == default_proj), 0)
items = rows + [("__MANUAL__", "Enter a Project ID manually...")]
win = min(10, len(items))
top = max(0, min(idx - win // 2, len(items) - win))

tty_r = open("/dev/tty", "rb", buffering=0)
tty_w = open("/dev/tty", "w", encoding="utf-8", buffering=1)
fd = tty_r.fileno()
old_attrs = termios.tcgetattr(fd)
drawn = 0

def render():
    global drawn, top
    if idx < top:
        top = idx
    elif idx >= top + win:
        top = idx - win + 1
    out = []
    if drawn > 0:
        out.append(f"\033[{drawn}A")
    header = f"  \033[1;36m?\033[0m \033[1mChoose a GCP project for the app & {ops_ds}\033[0m \033[2m(↑/↓ arrows, Enter to select)\033[0m"
    out.append("\r\033[2K" + header + "\n")
    lines = 1
    for pos in range(top, top + win):
        pid, pname = items[pos]
        sel = (pos == idx)
        if pid == "__MANUAL__":
            label = f"✎ {pname}"
        else:
            label = f"{pid:<34} ({pname})"
        if sel:
            out.append(f"\r\033[2K    \033[1;36m❯ {label}\033[0m\n")
        else:
            out.append(f"\r\033[2K      {label}\n")
        lines += 1
    if len(items) > win:
        hint = f"      \033[2mShowing {top + 1}-{top + win} of {len(items) - 1} projects\033[0m"
        out.append("\r\033[2K" + hint + "\n")
        lines += 1
    tty_w.write("".join(out))
    tty_w.flush()
    drawn = lines

chosen = None
try:
    tty_w.write("\033[?25l")
    tty_w.flush()
    tty.setraw(fd)
    render()
    while True:
        ch = os.read(fd, 1)
        if not ch:
            break
        if ch == b"\x03":  # Ctrl+C
            chosen = "__ABORT__"
            break
        if ch in (b"\r", b"\n"):
            chosen = items[idx][0]
            break
        if ch in (b"k", b"K"):
            idx = (idx - 1) % len(items)
            render()
        elif ch in (b"j", b"J"):
            idx = (idx + 1) % len(items)
            render()
        elif ch == b"\x1b":
            if select.select([fd], [], [], 0.05)[0]:
                seq = os.read(fd, 2)
                if seq in (b"[A", b"OA"):
                    idx = (idx - 1) % len(items)
                    render()
                elif seq in (b"[B", b"OB"):
                    idx = (idx + 1) % len(items)
                    render()
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
    tty_w.write("\033[?25h")
    tty_w.flush()

if chosen == "__ABORT__":
    tty_w.write("\n")
    tty_w.flush()
    sys.exit(130)

if chosen == "__MANUAL__":
    tty_w.write("  Enter the GCP Project ID to install into: ")
    tty_w.flush()
    line = tty_r.readline().decode("utf-8", errors="ignore").strip()
    chosen = "".join(line.split())

tty_r.close()
tty_w.close()
if chosen:
    print(chosen)
PY
)" || _pick_rc=$?
      if (( _pick_rc == 130 )); then
        echo "❌ Setup cancelled." >&2
        exit 130
      fi
    fi

    # Fallback numbered picker (for non-raw TTYs or automated piped tests)
    if [[ -z "$PROJECT_ID" ]]; then
      _PROJ_IDS=()
      _PROJ_NAMES=()
      while IFS=$'\t' read -r _pid _pname; do
        [[ -z "$_pid" ]] && continue
        _PROJ_IDS+=("$_pid")
        _PROJ_NAMES+=("${_pname:-$_pid}")
      done <<< "$_proj_tsv"

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
  fi
  if [[ -z "$PROJECT_ID" ]]; then
    echo "❌ No GCP Project selected. Pick a project when prompted or pass --project=YOUR_PROJECT_ID" >&2
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

  # Enable APIs via BatchEnable (never calls serviceusage.Services.List, avoiding Cloud Shell's
  # shared 618104708054 List quota limit). Includes automatic backoff if transient 429 occurs.
  step "[2/7] Enabling required Google Cloud APIs (~30-60s on a fresh project)..."
  local -a _req_apis=(
    cloudresourcemanager.googleapis.com
    serviceusage.googleapis.com
    iam.googleapis.com
    bigquery.googleapis.com
    bigquerydatatransfer.googleapis.com
    bigquerystorage.googleapis.com
    bigqueryreservation.googleapis.com
    recommender.googleapis.com
    cloudasset.googleapis.com
    run.googleapis.com
    cloudbuild.googleapis.com
    artifactregistry.googleapis.com
    cloudscheduler.googleapis.com
    iap.googleapis.com
    aiplatform.googleapis.com
  )
  local _api_log
  _api_log="$(mktemp "${TMPDIR:-/tmp}/bqopt_apis.XXXXXX")"
  if ! gcloud services enable "${_req_apis[@]}" --project="$PROJECT_ID" --quiet 2>"$_api_log"; then
    local _api_ok="false"
    if grep -qiE "429|RESOURCE_EXHAUSTED|rateLimitExceeded|Quota exceeded" "$_api_log"; then
      for _api_wait in 10 20; do
        warn "Service Usage API rate-limited; retrying in ${_api_wait}s..."
        sleep "${BQOPT_API_RETRY_SLEEP_SECONDS:-$_api_wait}"
        if gcloud services enable "${_req_apis[@]}" --project="$PROJECT_ID" --billing-project="$PROJECT_ID" --quiet 2>"$_api_log" \
           || gcloud services enable "${_req_apis[@]}" --project="$PROJECT_ID" --quiet 2>"$_api_log"; then
          _api_ok="true"
          break
        fi
      done
    fi
    if [[ "$_api_ok" != "true" ]]; then
      cat "$_api_log" >&2
      rm -f "$_api_log"
      exit 1
    fi
  fi
  rm -f "$_api_log"
  ok "All required Google Cloud APIs enabled."

  EXISTING_BEFORE="false"
  if [[ "$PHASE" == "prepare" ]]; then
    if gcloud run services describe "$SERVICE_NAME" --project="$PROJECT_ID" --region="$RUN_REGION" --quiet >/dev/null 2>&1; then
      EXISTING_BEFORE="true"
    fi
  fi

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

  # ---- Step 3: Service Account & IAM -----------------------------------------
  step "[3/7] Creating Service Account & granting permissions..."
  SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

  if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    gcloud iam service-accounts create "$SA_NAME" \
      --project="$PROJECT_ID" \
      --display-name="BigQuery Optimization Control Plane" \
      --description="Runs the BQ Optimization Review UI and nightly telemetry/rules pipeline" \
      --quiet
    ok "Created service account ${SA_EMAIL} (waiting ${BQOPT_SA_SLEEP_SECONDS:-10}s for IAM propagation)..."
    sleep "${BQOPT_SA_SLEEP_SECONDS:-10}"
  else
    ok "Service account ${SA_EMAIL} already exists."
  fi

  HAD_RUN_BUILDER="false"
  if gcloud projects get-iam-policy "$PROJECT_ID" \
       --flatten="bindings[].members" \
       --filter="bindings.role:roles/run.builder AND bindings.members:serviceAccount:${SA_EMAIL}" \
       --format="value(bindings.role)" --quiet 2>/dev/null | tr -d '[:space:]' | python3 -c "import sys; sys.exit(0 if sys.stdin.read().strip() else 1)"; then
    HAD_RUN_BUILDER="true"
  fi

  PROJECT_ROLES=(
    "roles/run.builder"
    "roles/storage.objectViewer"
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

  # Detect whether a local Docker daemon is available (standard in Cloud Shell) so we can build &
  # push directly with user credentials, skipping Cloud Build gs://run-sources IAM cache delays.
  local _has_local_docker="false"
  if [[ -z "$PREBUILT_IMAGE" && "${BQOPT_SKIP_LOCAL_DOCKER:-0}" != "1" ]] \
     && command -v docker >/dev/null 2>&1 \
     && docker info >/dev/null 2>&1; then
    _has_local_docker="true"
  fi

  # If Cloud Run's source staging bucket already exists (standalone `--source=.` installs),
  # bind storage.objectViewer directly on the bucket so GCS refreshes its bucket IAM etag immediately.
  SOURCE_BUCKET="gs://run-sources-${PROJECT_ID}-${RUN_REGION}"
  if [[ -z "$PREBUILT_IMAGE" && "$_has_local_docker" == "false" ]] && gcloud storage buckets describe "$SOURCE_BUCKET" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    gcloud storage buckets add-iam-policy-binding "$SOURCE_BUCKET" \
      --member="serviceAccount:${SA_EMAIL}" \
      --role="roles/storage.objectViewer" \
      --quiet >/dev/null 2>&1 || true
  fi

  if [[ -z "$PREBUILT_IMAGE" && "$_has_local_docker" == "false" && "$HAD_RUN_BUILDER" == "false" ]]; then
    ok "Granted project-level Cloud Run Builder, Storage, BigQuery, Recommender & Vertex AI roles (waiting ${BQOPT_IAM_SLEEP_SECONDS:-15}s for IAM propagation)..."
    sleep "${BQOPT_IAM_SLEEP_SECONDS:-15}"
  else
    ok "Granted project-level Cloud Run Builder, Storage, BigQuery, Recommender & Vertex AI roles on ${PROJECT_ID}."
  fi

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
      # Optional org-level metadata & recommender viewers for cross-project table cards (best-effort)
      gcloud organizations add-iam-policy-binding "$ORG_ID" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/bigquery.metadataViewer" \
        --condition=None --quiet >/dev/null 2>&1 || true
      gcloud organizations add-iam-policy-binding "$ORG_ID" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/recommender.bigqueryPartitionClusterViewer" \
        --condition=None --quiet >/dev/null 2>&1 || true
      ORG_IAM_READY="true"
      ok "Granted org-wide read-only discovery & table metadata roles (cloudasset, resourceViewer, metadataViewer)."
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

  # ---- Step 4: Generate production config.yaml -------------------------------
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

  write_config_yaml "$IAP_AUDIENCE"
  ok "Wrote config.yaml (Admin: ${ADMIN_EMAIL}, Scope: ${ASSET_SCOPE}, View: ${JOBS_VIEW})."
}

build_service_env_vars() {
  local _aud="$1"
  local _vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},BQOPT_PIPELINE_JOB=${JOB_NAME},BQOPT_JOB_REGION=${RUN_REGION}"
  if [[ -n "$_aud" ]]; then
    _vars="${_vars},IAP_AUDIENCE=${_aud}"
  else
    _vars="${_vars},IAP_AUDIENCE="
  fi
  printf '%s' "$_vars"
}

run_precreate_step() {
  load_prepared_state
  if [[ -z "$PREBUILT_IMAGE" ]]; then
    echo "❌ --phase=precreate requires --image=IMAGE_URI" >&2
    exit 1
  fi
  step "[5/7] Creating Cloud Run service ${SERVICE_NAME} with dedicated service account ${SA_EMAIL}..."
  local _env_vars
  _env_vars="$(build_service_env_vars "$IAP_AUDIENCE")"
  gcloud run deploy "$SERVICE_NAME" \
    --project="$PROJECT_ID" \
    --region="$RUN_REGION" \
    --image="$PREBUILT_IMAGE" \
    --service-account="$SA_EMAIL" \
    --memory=1Gi \
    --cpu=1 \
    --timeout=900 \
    --no-allow-unauthenticated \
    --set-env-vars="$_env_vars" \
    --quiet
  ok "Created base Cloud Run service ${SERVICE_NAME} running as ${SA_EMAIL}."
}

run_finalize_steps() {
  local _deploy_log
  _deploy_log="$(mktemp "${TMPDIR:-/tmp}/bqopt_deploy.XXXXXX")"
  # Clean up temp log when function returns.
  # shellcheck disable=SC2064
  trap "rm -f '$_deploy_log'" RETURN

  local _source_bucket="gs://run-sources-${PROJECT_ID}-${RUN_REGION}"
  local _build_sa_resource="projects/${PROJECT_ID}/serviceAccounts/${SA_EMAIL}"

  deploy_ui_revision() {
    local _use_iap="$1"
    local _env_str="$2"
    if [[ -n "$PREBUILT_IMAGE" ]]; then
      local -a _img_cmd=(
        gcloud run deploy "$SERVICE_NAME"
        --project="$PROJECT_ID"
        --region="$RUN_REGION"
        --image="$PREBUILT_IMAGE"
        --service-account="$SA_EMAIL"
        --memory=1Gi
        --cpu=1
        --timeout=900
        --no-allow-unauthenticated
        --set-env-vars="$_env_str"
        --quiet
      )
      if [[ "$_use_iap" == "true" ]]; then
        _img_cmd+=(--iap)
      fi
      "${_img_cmd[@]}" 2>&1 | tee "$_deploy_log"
      return $?
    fi

    local -a _cmd=(
      gcloud run deploy "$SERVICE_NAME"
      --project="$PROJECT_ID"
      --region="$RUN_REGION"
      --source=.
      --build-service-account="$_build_sa_resource"
      --service-account="$SA_EMAIL"
      --memory=1Gi
      --cpu=1
      --timeout=900
      --no-allow-unauthenticated
      --set-env-vars="$_env_str"
      --quiet
    )
    if [[ "$_use_iap" == "true" ]]; then
      _cmd+=(--iap)
    fi

    if "${_cmd[@]}" 2>&1 | tee "$_deploy_log"; then
      return 0
    fi

    # Newly created (or deleted-and-recreated) service accounts can take 60-120s for GCS / Cloud Build
    # IAM caches to propagate storage.objects.get on gs://run-sources-<project>-<region>.
    local -a _waits=(15 20 25 30 30 30)
    local _total="${#_waits[@]}"
    local _idx=0
    local _wait
    for _wait in "${_waits[@]}"; do
      _idx=$((_idx + 1))
      if ! python3 - "$_deploy_log" <<'PY'
import sys
txt = open(sys.argv[1], encoding="utf-8", errors="ignore").read().lower()
sys.exit(0 if ("storage.objects.get" in txt or "artifactregistry" in txt or "permission" in txt) else 1)
PY
      then
        break
      fi
      if gcloud storage buckets describe "$_source_bucket" --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
        gcloud storage buckets add-iam-policy-binding "$_source_bucket" \
          --member="serviceAccount:${SA_EMAIL}" \
          --role="roles/storage.objectViewer" \
          --quiet >/dev/null 2>&1 || true
      fi
      warn "Waiting ${_wait}s for Google Cloud Storage / Builder IAM propagation (retry ${_idx}/${_total})..."
      sleep "$_wait"
      if "${_cmd[@]}" 2>&1 | tee "$_deploy_log"; then
        return 0
      fi
    done
    return 1
  }

  # ---- Step 5: Deploy Cloud Run Web UI (+ Direct IAP) ------------------------
  if [[ -n "$PREBUILT_IMAGE" ]]; then
    step "[5/7] Configuring Web UI on Cloud Run & enabling Direct Identity-Aware Proxy (IAP)..."
  elif [[ "${BQOPT_SKIP_LOCAL_DOCKER:-0}" != "1" ]] \
       && command -v docker >/dev/null 2>&1 \
       && docker info >/dev/null 2>&1; then
    step "[5/7] Building container image locally in Cloud Shell & deploying Web UI (~2-3 minutes)..."
    local _ar_repo="cloud-run-source-deploy"
    local _local_img="${RUN_REGION}-docker.pkg.dev/${PROJECT_ID}/${_ar_repo}/${SERVICE_NAME}:latest"
    if ! gcloud artifacts repositories describe "$_ar_repo" --project="$PROJECT_ID" --location="$RUN_REGION" --quiet >/dev/null 2>&1; then
      gcloud artifacts repositories create "$_ar_repo" \
        --project="$PROJECT_ID" \
        --location="$RUN_REGION" \
        --repository-format=docker \
        --description="Cloud Run source deployments" \
        --quiet >/dev/null 2>&1 || true
    fi
    gcloud auth configure-docker "${RUN_REGION}-docker.pkg.dev" --quiet >/dev/null 2>&1 || true
    if docker build -t "$_local_img" . && docker push "$_local_img"; then
      PREBUILT_IMAGE="$_local_img"
      ok "Built & pushed container image ${_local_img}."
    else
      warn "Local Docker build/push did not finish cleanly — falling back to Cloud Run source build..."
    fi
  else
    step "[5/7] Building & deploying the Web UI to Cloud Run (~2-3 minutes)..."
  fi

  local _env_vars
  _env_vars="$(build_service_env_vars "$IAP_AUDIENCE")"

  local _deploy_iap_active="false"
  if [[ "$ENABLE_IAP" == "true" ]]; then
    # Provision the Google-managed IAP Service Agent so IAP can invoke the private Cloud Run service.
    gcloud beta services identity create --service=iap.googleapis.com --project="$PROJECT_ID" --quiet >/dev/null 2>&1 || true

    if deploy_ui_revision "true" "$_env_vars"; then
      _deploy_iap_active="true"
    elif python3 - "$_deploy_log" <<'PY'
import sys
txt = open(sys.argv[1], encoding="utf-8", errors="ignore").read().lower()
# Only fall back when the failure is actually IAP-related (never on a source/Docker build failure).
is_iap_err = ("iap" in txt) and ("invalid build request" not in txt) and ("storage.objects.get" not in txt)
sys.exit(0 if is_iap_err else 1)
PY
    then
      warn "Direct --iap flag was rejected by project/org policy; redeploying private service without --iap flag..."
      IAP_AUDIENCE=""
      if [[ -z "$PREBUILT_IMAGE" ]]; then
        write_config_yaml ""
      fi
      deploy_ui_revision "false" "$(build_service_env_vars "")"
    else
      echo "❌ Cloud Run deployment failed (see error above)." >&2
      exit 1
    fi
  else
    deploy_ui_revision "false" "$_env_vars"
  fi

  if [[ "$_deploy_iap_active" == "true" ]]; then
    local _iap_sa="service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com"
    if ! gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
         --project="$PROJECT_ID" \
         --region="$RUN_REGION" \
         --member="serviceAccount:${_iap_sa}" \
         --role="roles/run.invoker" \
         --quiet >/dev/null 2>&1; then
      sleep "${BQOPT_IAP_SLEEP_SECONDS:-5}"
      gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
        --project="$PROJECT_ID" \
        --region="$RUN_REGION" \
        --member="serviceAccount:${_iap_sa}" \
        --role="roles/run.invoker" \
        --quiet >/dev/null
    fi

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

  local _service_url _image_uri
  _service_url="$(gcloud run services describe "$SERVICE_NAME" --project="$PROJECT_ID" --region="$RUN_REGION" --format="value(status.url)" --quiet)"
  _image_uri="$(gcloud run services describe "$SERVICE_NAME" --project="$PROJECT_ID" --region="$RUN_REGION" --format="value(spec.template.spec.containers[0].image)" --quiet)"
  if [[ -z "$_image_uri" ]]; then
    _image_uri="$(gcloud run services describe "$SERVICE_NAME" --project="$PROJECT_ID" --region="$RUN_REGION" --format="value(template.containers[0].image)" --quiet)"
  fi
  if [[ -z "$_image_uri" && -n "$PREBUILT_IMAGE" ]]; then
    _image_uri="$PREBUILT_IMAGE"
  fi
  if [[ -z "$_image_uri" ]]; then
    echo "❌ Could not read deployed container image URI from ${SERVICE_NAME}." >&2
    exit 1
  fi
  ok "Web UI deployed at: ${_service_url}"

  # ---- Step 6: Nightly Cloud Run Job + BigQuery Schema Init ------------------
  step "[6/7] Creating Nightly Pipeline Job & initializing BigQuery dataset (${OPS_DATASET})..."

  gcloud run jobs deploy "$JOB_NAME" \
    --project="$PROJECT_ID" \
    --region="$RUN_REGION" \
    --image="$_image_uri" \
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

  # Enable BigQuery INFORMATION_SCHEMA.TABLE_STORAGE views across active regions so Setup Check is green out of the box.
  local _discovered_locs _storage_regions
  _discovered_locs="$(gcloud asset search-all-resources \
    --scope="$ASSET_SCOPE" \
    --asset-types="bigquery.googleapis.com/Dataset" \
    --read-mask="location" \
    --limit=500 \
    --format="value(location)" \
    --quiet 2>/dev/null || true)"
  if [[ -z "$_discovered_locs" && "$ASSET_SCOPE" != "projects/${PROJECT_ID}" ]]; then
    _discovered_locs="$(gcloud asset search-all-resources \
      --scope="projects/${PROJECT_ID}" \
      --asset-types="bigquery.googleapis.com/Dataset" \
      --read-mask="location" \
      --limit=500 \
      --format="value(location)" \
      --quiet 2>/dev/null || true)"
  fi
  _storage_regions="$(printf '%s\n%s\n%s\n' "$BQ_LOCATION" "$RUN_REGION" "$_discovered_locs" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' \
    | awk 'NF && !seen[$0]++')"
  local -a _enabled_storage_regions=()
  while IFS= read -r _R; do
    [[ -z "$_R" ]] && continue
    if [[ -n "$ORG_ID" && "$ORG_IAM_READY" == "true" ]]; then
      bq --quiet --project_id="$PROJECT_ID" --location="$_R" query --use_legacy_sql=false \
        "ALTER ORGANIZATION SET OPTIONS (\`region-${_R}.enable_info_schema_storage\` = TRUE);" >/dev/null 2>&1 || true
    fi
    if bq --quiet --project_id="$PROJECT_ID" --location="$_R" query --use_legacy_sql=false \
         "ALTER PROJECT \`${PROJECT_ID}\` SET OPTIONS (\`region-${_R}.enable_info_schema_storage\` = TRUE);" >/dev/null 2>&1; then
      _enabled_storage_regions+=("$_R")
    fi
  done <<< "$_storage_regions"
  if [[ "${#_enabled_storage_regions[@]}" -gt 0 ]]; then
    ok "Enabled BigQuery TABLE_STORAGE size views in: ${_enabled_storage_regions[*]}"
  fi

  # ---- Step 7: Nightly Cloud Scheduler (03:00 UTC, inside [3, 9] window) -----
  step "[7/7] Scheduling automatic nightly scans (03:00 UTC)..."

  local _scheduler_uri="https://${RUN_REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"
  if gcloud scheduler jobs describe "$SCHEDULER_NAME" --project="$PROJECT_ID" --location="$RUN_REGION" --quiet >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$SCHEDULER_NAME" \
      --project="$PROJECT_ID" \
      --location="$RUN_REGION" \
      --schedule="0 3 * * *" \
      --time-zone="Etc/UTC" \
      --uri="$_scheduler_uri" \
      --http-method=POST \
      --oauth-service-account-email="$SA_EMAIL" \
      --quiet >/dev/null
  else
    gcloud scheduler jobs create http "$SCHEDULER_NAME" \
      --project="$PROJECT_ID" \
      --location="$RUN_REGION" \
      --schedule="0 3 * * *" \
      --time-zone="Etc/UTC" \
      --uri="$_scheduler_uri" \
      --http-method=POST \
      --oauth-service-account-email="$SA_EMAIL" \
      --description="Nightly BigQuery Optimization Control Plane run (collect -> rules -> execute approved -> verify)" \
      --quiet >/dev/null
  fi
  ok "Scheduled ${SCHEDULER_NAME} every night at 03:00 UTC (inside 03:00-09:00 UTC change window)."

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

  rm -f "$STATE_FILE"

  banner "🎉 Setup Complete — Your App Is Live!"
  echo ""
  echo "  👉 Click here to open Settings & run your first scan:"
  echo "     ${_service_url}/#settings"
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
}

# ---- Dispatch lifecycle phase ------------------------------------------------
case "$PHASE" in
  prepare)
    run_prepare_steps
    save_prepared_state
    ;;
  postbuild)
    if [[ ! -f "$STATE_FILE" ]]; then
      echo "❌ Earlier setup preparation step failed — aborting image push." >&2
      if [[ -n "$PREBUILT_IMAGE" ]]; then
        docker rmi -f "$PREBUILT_IMAGE" >/dev/null 2>&1 || true
      fi
      exit 1
    fi
    load_prepared_state
    if [[ "$EXISTING_BEFORE" == "true" ]]; then
      # On redeploy (`existingService != nil`), cloud-run-button skips `precreate` & `postcreate`.
      # Push the built image and finalize IAP + Job + BigQuery schema + Scheduler right here.
      _POSTBUILD_DONE="false"
      trap 'if [[ "$_POSTBUILD_DONE" != "true" && -n "$PREBUILT_IMAGE" ]]; then docker rmi -f "$PREBUILT_IMAGE" >/dev/null 2>&1 || true; fi' EXIT
      step "Pushing updated container image to Artifact Registry..."
      docker push "$PREBUILT_IMAGE"
      run_finalize_steps
      _POSTBUILD_DONE="true"
    fi
    ;;
  precreate)
    run_precreate_step
    ;;
  finalize)
    load_prepared_state
    run_finalize_steps
    ;;
  all)
    run_prepare_steps
    run_finalize_steps
    ;;
esac
