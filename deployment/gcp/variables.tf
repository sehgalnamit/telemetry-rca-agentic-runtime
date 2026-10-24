# =============================================================================
# ILLUSTRATIVE ARCHITECTURE TEMPLATE -- NOT APPLIED TO ANY LIVE GCP PROJECT.
# See versions.tf and docs/GCP_DEPLOYMENT.md for the full disclaimer.
# =============================================================================

variable "project_id" {
  description = "GCP project ID that will host the RCA CONTROL PLANE (Cloud Run service, Artifact Registry, Secret Manager). This is the RCA operator's own project -- never a tenant's project."
  type        = string
}

variable "region" {
  description = "GCP region for all control-plane resources."
  type        = string
  default     = "us-central1"
}

variable "environment_name" {
  description = "Short name used to derive resource names, e.g. \"rca-dev\". Keep it short: it feeds into names with tight length limits (service account account_id is capped at 30 characters)."
  type        = string
  default     = "rca-dev"

  validation {
    condition     = length(var.environment_name) <= 15
    error_message = "environment_name must be 15 characters or fewer so derived resource names stay within GCP length limits."
  }
}

variable "container_image" {
  description = "Container image to run initially. Defaults to a small public placeholder so the first deployment succeeds before any image has been pushed to the Artifact Registry repository provisioned here. Replace with \"<region>-docker.pkg.dev/<project_id>/<repo>/telemetry-rca-agentic-runtime:<tag>\" after building the Dockerfile at the repo root and pushing it."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "container_cpu" {
  description = "CPU limit for the single control-plane container, in Cloud Run's \"number of vCPUs\" string format."
  type        = string
  default     = "1"
}

variable "container_memory" {
  description = "Memory limit for the single control-plane container."
  type        = string
  default     = "512Mi"
}

variable "min_instances" {
  description = "Minimum Cloud Run instance count. 0 allows scale-to-zero between RCA runs in a non-production demo; 1 keeps the control plane warm."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum Cloud Run instance count."
  type        = number
  default     = 2
}

variable "labels" {
  description = "Labels applied to every resource in this module."
  type        = map(string)
  default = {
    project           = "telemetry-rca-agentic-runtime"
    component         = "rca-control-plane"
    deployment-status = "illustrative-template-only"
  }
}

# ---------------------------------------------------------------------------
# DATA-PLANE ILLUSTRATION ONLY (see main.tf for the resource that uses
# these). Both are empty by default, which keeps the illustrative
# tenant-delegation resource a no-op (count = 0) unless a real tenant
# project/dataset is supplied -- this module never assumes it can reach
# another GCP project.
# ---------------------------------------------------------------------------

variable "tenant_project_id" {
  description = "ILLUSTRATIVE ONLY. Project ID of a tenant's own GCP project that exposes telemetry via BigQuery (e.g. queried in place through BigQuery Omni, or shared via Analytics Hub / BigLake authorized views). Left empty by default so the illustrative IAM-binding resource in main.tf is skipped; a real cross-tenant grant requires the tenant's own consent and Terraform/gcloud session, not this module's credentials."
  type        = string
  default     = ""
}

variable "tenant_dataset_id" {
  description = "ILLUSTRATIVE ONLY. BigQuery dataset ID within tenant_project_id that the control plane would be granted least-privilege, read-only access to. See tenant_project_id."
  type        = string
  default     = ""
}
