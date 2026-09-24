# Starter Terraform for bq-optimizer — SAs, IAM, Cloud Run services/jobs, schedules.
# The ops dataset + tables come from sql/ (run `python -m optimizer.cli init`),
# keeping DDL in one place; Terraform owns identities and runtime.

terraform {
  required_providers { google = { source = "hashicorp/google", version = ">= 5.30" } }
}
provider "google" { project = var.project_id }

# --- identities: reader vs executor, never combined -------------------------
resource "google_service_account" "collector" {
  account_id   = "bqopt-collector"
  display_name = "bq-optimizer collector (read-only)"
}
resource "google_service_account" "executor" {
  account_id   = "bqopt-executor"
  display_name = "bq-optimizer executor (scoped writes)"
}

locals {
  collector_roles = [
    "roles/bigquery.jobUser",
    "roles/bigquery.metadataViewer",
    "roles/bigquery.resourceViewer",
    "roles/recommender.bigqueryPartitionClusterViewer",
    "roles/aiplatform.user", # Required for Engine 3 (Vertex AI Gemini 2.5 Flash SQL Judge)
  ]
}
resource "google_project_iam_member" "collector" {
  for_each = toset(local.collector_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.collector.email}"
}

# Executor: jobUser project-wide, data roles PER DATASET ONLY.
resource "google_project_iam_member" "executor_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.executor.email}"
}
# Example per-dataset grant — repeat per dataset the executor may touch:
# resource "google_bigquery_dataset_iam_member" "executor_sales" {
#   dataset_id = "sales"
#   role       = "roles/bigquery.dataOwner"   # needed for IAM rebind on swap
#   member     = "serviceAccount:${google_service_account.executor.email}"
# }

# --- Cloud Armor Web Application Firewall (WAF) Policy (OWASP Top 10 Protection) ---
resource "google_compute_security_policy" "review_waf_policy" {
  name        = "bqopt-review-waf-policy"
  description = "Cloud Armor WAF policy protecting Review UI against SQLi, XSS, and LFI"
  rule {
    action   = "deny(403)"
    priority = 1000
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable') || evaluatePreconfiguredWaf('xss-v33-stable')"
      }
    }
    description = "Block OWASP SQL Injection and Cross-Site Scripting payloads"
  }
  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config { src_ip_ranges = ["*"] }
    }
    description = "Default allow behind IAP"
  }
}

# --- review UI (behind IAP — wire an LB + IAP in front) ---------------------
resource "google_cloud_run_v2_service" "review" {
  name     = "bqopt-review"
  location = var.region
  template {
    service_account = google_service_account.executor.email
    containers { image = var.image }
  }
  ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
}

# --- workers as Cloud Run jobs on schedules ---------------------------------
locals {
  jobs = {
    rules   = { args = ["-m", "optimizer.cli", "rules"],            schedule = "0 6 * * *" }
    execute = { args = ["-m", "optimizer.cli", "execute"],          schedule = "30 3 * * *" } # inside change window
    verify  = { args = ["-m", "optimizer.cli", "verify"],           schedule = "0 7 * * *" }
    driver  = { args = ["-m", "optimizer.cli", "collect-backfill"], schedule = "30 5 * * *" }
    syncrec = { args = ["-m", "optimizer.cli", "sync-recommender"], schedule = "0 8 * * *" }
  }
}
resource "google_cloud_run_v2_job" "worker" {
  for_each = local.jobs
  name     = "bqopt-${each.key}"
  location = var.region
  template {
    template {
      service_account = google_service_account.executor.email
      containers {
        image   = var.image
        command = ["python"]
        args    = each.value.args
      }
    }
  }
}
resource "google_cloud_scheduler_job" "trigger" {
  for_each  = local.jobs
  name      = "bqopt-${each.key}"
  region    = var.region
  schedule  = each.value.schedule
  time_zone = "Etc/UTC"
  http_target {
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/bqopt-${each.key}:run"
    http_method = "POST"
    oauth_token { service_account_email = google_service_account.executor.email }
  }
}
