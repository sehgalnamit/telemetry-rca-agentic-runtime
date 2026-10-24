# =============================================================================
# ILLUSTRATIVE ARCHITECTURE TEMPLATE -- NOT APPLIED TO ANY LIVE GCP PROJECT.
# See versions.tf and docs/GCP_DEPLOYMENT.md for the full disclaimer.
#
# CONTROL PLANE vs. DATA PLANE
# -----------------------------------------------------------------------
# This module provisions the RCA CONTROL PLANE only:
#   - A Cloud Run v2 service running the agent runtime, rule engine, jury
#     evaluator, and audit log (agent/, rules/, evaluator/, audit/ in this
#     repo).
#   - An Artifact Registry repository holding that control plane's own
#     image.
#   - A Secret Manager secret holding the control plane's own secrets
#     (e.g. an audit-log signing key) -- never tenant data or tenant
#     credentials.
#   - IAM bindings letting the control plane's own service account write
#     its own operational metrics/traces to Cloud Monitoring and Cloud
#     Trace in THIS project. Cloud Monitoring/Trace themselves are
#     per-project platform services enabled automatically; there is no
#     separate "workspace" resource to create here the way there is for
#     Azure Log Analytics.
#
# It deliberately does NOT provision the DATA PLANE: each tenant's own
# telemetry (the logs/traces/metrics tables modeled in federation/ and
# otel_mock/) lives in that TENANT's own GCP project, typically as:
#   - A BigQuery dataset queried in place via BigQuery Omni (cross-cloud /
#     cross-region query-in-place, no data movement), or
#   - A BigLake authorized view over data in the tenant's own GCS bucket,
#     or
#   - A listing shared through Analytics Hub.
#
# Cross-project access to those stores is modeled via tenant-granted,
# least-privilege IAM bindings scoped to a single dataset -- see the
# `google_bigquery_dataset_iam_member.tenant_delegated_read_access`
# resource below, which is a no-op (count = 0) unless a real tenant project
# ID is supplied. This mirrors the zero-copy federation model implemented
# locally with DuckDB ATTACH in federation/query_layer.py.
# =============================================================================

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  name_prefix = var.environment_name

  required_apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "monitoring.googleapis.com",
    "cloudtrace.googleapis.com",
  ]
}

# ---------------------------------------------------------------------------
# Platform APIs. Cloud Monitoring and Cloud Trace are enabled per-project;
# there is no separate workspace resource to provision (unlike Azure Log
# Analytics). Enabling them here is what lets the service account below
# write metrics/traces at all.
# ---------------------------------------------------------------------------
resource "google_project_service" "required" {
  for_each = toset(local.required_apis)

  project                    = var.project_id
  service                    = each.value
  disable_dependent_services = false
  disable_on_destroy         = false
}

# ---------------------------------------------------------------------------
# Control-plane identity. No long-lived key is created for it; Cloud Run
# injects workload identity credentials automatically.
# ---------------------------------------------------------------------------
resource "google_service_account" "control_plane" {
  project      = var.project_id
  account_id   = "${local.name_prefix}-rca-runtime"
  display_name = "RCA control-plane runtime (agent, rule engine, jury evaluator, audit log)"
}

resource "google_project_iam_member" "control_plane_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.control_plane.email}"
}

resource "google_project_iam_member" "control_plane_trace_agent" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.control_plane.email}"
}

# ---------------------------------------------------------------------------
# Artifact Registry: holds the control plane's own container image (built
# from the Dockerfile at the repo root). Analogous to the Azure Container
# Registry in ../azure/modules/registry.bicep.
# ---------------------------------------------------------------------------
resource "google_artifact_registry_repository" "control_plane" {
  project       = var.project_id
  location      = var.region
  repository_id = "${local.name_prefix}-rca-runtime"
  format        = "DOCKER"
  description   = "Container images for the RCA control-plane runtime (see Dockerfile at repo root)."

  depends_on = [google_project_service.required]
}

# ---------------------------------------------------------------------------
# Secret Manager: holds control-plane secrets only. No secret *value* is
# set here on purpose -- an operator seeds it after deployment (e.g. via
# `gcloud secrets versions add`), the same way the Key Vault secret in the
# Azure template is left for the operator to seed.
# ---------------------------------------------------------------------------
resource "google_secret_manager_secret" "audit_log_signing_key" {
  project   = var.project_id
  secret_id = "${local.name_prefix}-audit-log-signing-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "control_plane_secret_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.audit_log_signing_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.control_plane.email}"
}

# ---------------------------------------------------------------------------
# Cloud Run v2 service: the RCA control plane itself.
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "control_plane" {
  name     = "${local.name_prefix}-rca-runtime"
  location = var.region
  project  = var.project_id
  labels   = var.labels

  # Demo/illustrative deployment: don't block `terraform destroy`.
  deletion_protection = false

  # Not internet-facing by default -- mirrors the `external: false` ingress
  # choice in the Azure Container App. Front it with your own load
  # balancer / IAP / VPC-SC boundary if you need a public endpoint for a
  # real deployment.
  ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.control_plane.email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.container_image

      resources {
        limits = {
          cpu    = var.container_cpu
          memory = var.container_memory
        }
      }

      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }

      env {
        name = "AUDIT_LOG_SIGNING_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.audit_log_signing_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_project_service.required,
    google_project_iam_member.control_plane_metric_writer,
    google_project_iam_member.control_plane_trace_agent,
  ]
}

# ---------------------------------------------------------------------------
# DATA-PLANE ILLUSTRATION ONLY -- not wired into any resource above.
#
# This block models the pattern a TENANT would apply in THEIR OWN project
# to grant this control plane's service account least-privilege, read-only
# access to a BigQuery dataset exposed via BigQuery Omni / Analytics Hub /
# BigLake authorized views, without copying any data out of the tenant's
# project. It is a no-op (count = 0) unless both tenant_project_id and
# tenant_dataset_id are supplied, because a real grant requires the
# tenant's own Terraform/gcloud session and consent -- this module's own
# credentials are never sufficient to create it for real.
#
# An equivalent, dataset-ACL-based alternative is the dedicated
# `google_bigquery_dataset_access` resource (or an `access` block inside
# `google_bigquery_dataset`); either shape works for modeling an
# authorized-view-style grant instead of a project-IAM-style one.
# ---------------------------------------------------------------------------
resource "google_bigquery_dataset_iam_member" "tenant_delegated_read_access" {
  count = (var.tenant_project_id != "" && var.tenant_dataset_id != "") ? 1 : 0

  # NOTE: project/dataset_id below target the TENANT's own project and
  # dataset, not var.project_id (the RCA operator's project).
  project    = var.tenant_project_id
  dataset_id = var.tenant_dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.control_plane.email}"
}
