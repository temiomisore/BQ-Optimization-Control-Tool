#!/usr/bin/env bash
# Starter manual deploy script — for automated 1-click setup (Direct IAP, Cloud Run Job,
# Cloud Scheduler, and least-privilege IAM), prefer: ./scripts/install.sh --yes
set -euo pipefail
PROJECT=$(python3 -c "import re,sys,yaml; p=str(yaml.safe_load(open('config.yaml'))['project_id']); assert re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', p); print(p)")
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format="value(projectNumber)")
REGION=us-central1
CONFIG_JSON=$(python3 -c "import json,yaml; print(json.dumps(yaml.safe_load(open('config.yaml')), separators=(',',':')).replace('|','\\\\u007c'))")

# 1. Service accounts (least privilege: split reader/UI vs executor)
gcloud iam service-accounts create bqopt-collector --project "$PROJECT" || true
gcloud iam service-accounts create bqopt-executor  --project "$PROJECT" || true
for ROLE in roles/bigquery.resourceViewer roles/bigquery.metadataViewer \
            roles/recommender.bigqueryPartitionClusterViewer roles/bigquery.jobUser; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:bqopt-collector@${PROJECT}.iam.gserviceaccount.com" \
    --role "$ROLE" --condition=None -q
done
# Executor gets dataEditor/dataOwner ONLY on datasets it must touch — per-dataset, not project-wide.
echo ">> Grant bqopt-executor per-dataset roles via terraform/ or manually."

# 2. Ops schema + change sets + views
for F in sql/01_ops_schema.sql sql/04_change_sets.sql sql/03_derived_views.sql; do
  bq query --project_id="$PROJECT" --use_legacy_sql=false < "$F"
done

# 3. Daily collector: run `python -m optimizer.cli collect` as a Cloud Run Job (multi-region)
echo ">> Schedule Cloud Run Job for: python -m optimizer.cli pipeline"

# 4. Review UI on Cloud Run (runs as bqopt-collector, protected by Direct IAP + fail-closed IAP_AUDIENCE)
IAP_AUDIENCE="/projects/${PROJECT_NUMBER}/locations/${REGION}/services/bqopt-review"
gcloud beta run deploy bqopt-review --project "$PROJECT" --region "$REGION" --source . \
  --service-account "bqopt-collector@${PROJECT}.iam.gserviceaccount.com" \
  --no-allow-unauthenticated --iap \
  --set-env-vars="^|^GOOGLE_CLOUD_PROJECT=${PROJECT}|IAP_AUDIENCE=${IAP_AUDIENCE}|BQOPT_CONFIG_JSON=${CONFIG_JSON}"
echo ">> Workers as Cloud Run jobs on a schedule (only execute uses bqopt-executor):"
echo "   python -m optimizer.cli collect | rules | execute | verify | sync-recommender"
