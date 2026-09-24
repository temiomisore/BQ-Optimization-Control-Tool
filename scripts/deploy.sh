#!/usr/bin/env bash
# Starter deploy script — read before running; assumes gcloud auth + config.yaml done.
set -euo pipefail
PROJECT=$(python -c "import yaml;print(yaml.safe_load(open('config.yaml'))['project_id'])")
REGION=us-central1

# 1. Service accounts (least privilege: split reader vs executor)
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

# 3. Daily collector: paste sql/02_collector_run.sql into a scheduled query
echo ">> Create the scheduled query for sql/02_collector_run.sql (every 24h) in the console,"
echo "   or via bq mk --transfer_config with data_source=scheduled_query."

# 4. Review UI + workers on Cloud Run
gcloud run deploy bqopt-review --project "$PROJECT" --region "$REGION" --source . \
  --service-account "bqopt-executor@${PROJECT}.iam.gserviceaccount.com" --no-allow-unauthenticated
echo ">> Put IAP in front of bqopt-review; it has no auth of its own."
echo ">> Workers as Cloud Run jobs on a schedule:"
echo "   python -m optimizer.cli rules | execute | verify | sync-recommender | collect-backfill"
