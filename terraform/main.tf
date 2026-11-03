# Starter Terraform for bq-optimizer — SAs, IAM, Cloud Run services/jobs, schedules.
# The ops dataset + tables come from sql/ (run `python -m optimizer.cli init`),
# keeping DDL in one place; Terraform owns identities and runtime.

terraform {
  required_providers { google = { source = "hashicorp/google", version = ">= 5.30" } }
}
provider "google" { project = var.project_id }

data "google_project" "current" {
  project_id = var.project_id
}

# --- identities: reader vs executor vs scheduler, strictly separated ---------
resource "google_service_account" "collector" {
  account_id   = "bqopt-collector"
  display_name = "bq-optimizer collector & rules worker (read-only on customer data)"
}
resource "google_service_account" "executor" {
  account_id   = "bqopt-executor"
  display_name = "bq-optimizer executor (scoped DDL writes)"
}
resource "google_service_account" "scheduler" {
  account_id   = "bqopt-scheduler"
  display_name = "bq-optimizer Cloud Scheduler invoker"
}

locals {
  collector_roles = [
    "roles/bigquery.jobUser",
    "roles/bigquery.user",
    "roles/bigquery.metadataViewer",
    "roles/bigquery.resourceViewer",
    "roles/recommender.bigqueryPartitionClusterViewer",
    "roles/aiplatform.user", # Required for Engine 3 (Vertex AI Gemini 3.8 Flash SQL Judge)
    "roles/logging.viewer",  # Allows UI scan_runner to surface Cloud Run Job stderr on failure
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
# Grant both collector and executor dataEditor on optimizer_ops dataset once created:
# resource "google_bigquery_dataset_iam_member" "ops_writers" {
#   for_each   = toset([google_service_account.collector.email, google_service_account.executor.email])
#   dataset_id = "optimizer_ops"
#   role       = "roles/bigquery.dataEditor"
#   member     = "serviceAccount:${each.value}"
# }
# Example per-dataset grant — repeat per dataset the executor may touch:
# resource "google_bigquery_dataset_iam_member" "executor_sales" {
#   dataset_id = "sales"
#   role       = "roles/bigquery.dataOwner"   # needed for IAM rebind on swap
#   member     = "serviceAccount:${google_service_account.executor.email}"
# }

# --- Cloud Armor Web Application Firewall (WAF) Policy (attach to HTTPS LB backend) ---
resource "google_compute_security_policy" "review_waf_policy" {
  name        = "bqopt-review-waf-policy"
  description = "Cloud Armor WAF policy protecting Review UI against SQLi, XSS, and LFI (bind via google_compute_backend_service.security_policy)"
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

# --- review UI (behind Direct IAP or HTTPS LB + IAP + review_waf_policy) -----
resource "google_cloud_run_v2_service" "review" {
  name     = "bqopt-review"
  location = var.region
  template {
    service_account = google_service_account.collector.email
    containers {
      image = var.image
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "BQOPT_LOCATION"
        value = var.ops_location
      }
      env {
        name  = "IAP_AUDIENCE"
        value = "/projects/${data.google_project.current.number}/locations/${var.region}/services/bqopt-review"
      }
    }
  }
  ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
}

# --- workers as Cloud Run jobs on schedules ---------------------------------
locals {
  jobs = {
    collect = { args = ["-m", "optimizer.cli", "collect"],          schedule = "0 3 * * *",  writer = false }
    rules   = { args = ["-m", "optimizer.cli", "rules"],            schedule = "20 3 * * *", writer = false }
    execute = { args = ["-m", "optimizer.cli", "execute"],          schedule = "40 3 * * *", writer = true  } # inside [3, 9] UTC change window
    verify  = { args = ["-m", "optimizer.cli", "verify"],           schedule = "0 7 * * *",  writer = false }
    driver  = { args = ["-m", "optimizer.cli", "collect-backfill"], schedule = "30 5 * * *", writer = false }
    syncrec = { args = ["-m", "optimizer.cli", "sync-recommender"], schedule = "0 8 * * *",  writer = false }
  }
}
resource "google_cloud_run_v2_job" "worker" {
  for_each = local.jobs
  name     = "bqopt-${each.key}"
  location = var.region
  template {
    template {
      service_account = each.value.writer ? google_service_account.executor.email : google_service_account.collector.email
      containers {
        image   = var.image
        command = ["python"]
        args    = each.value.args
        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }
        env {
          name  = "BQOPT_LOCATION"
          value = var.ops_location
        }
      }
    }
  }
}
resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  for_each = local.jobs
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.worker[each.key].name
  role     = "roles/run.jobsExecutor"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
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
    oauth_token { service_account_email = google_service_account.scheduler.email }
  }
}
