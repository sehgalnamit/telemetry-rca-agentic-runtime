# =============================================================================
# ILLUSTRATIVE ARCHITECTURE TEMPLATE -- NOT APPLIED TO ANY LIVE GCP PROJECT.
# See versions.tf and docs/GCP_DEPLOYMENT.md for the full disclaimer.
# =============================================================================

output "control_plane_url" {
  description = "URL of the Cloud Run control-plane service (internal ingress only by default -- see main.tf)."
  value       = google_cloud_run_v2_service.control_plane.uri
}

output "control_plane_service_account_email" {
  description = "Email of the service account the control plane runs as. Used for IAM bindings, including any tenant-side delegated access grants (see docs/GCP_DEPLOYMENT.md)."
  value       = google_service_account.control_plane.email
}

output "artifact_registry_repository_id" {
  description = "Artifact Registry repository ID holding the control plane's own container image."
  value       = google_artifact_registry_repository.control_plane.repository_id
}

output "artifact_registry_repository_url" {
  description = "Full repository path to push images to, e.g. with `docker push`."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.control_plane.repository_id}"
}

output "secret_manager_secret_id" {
  description = "Secret Manager secret ID an operator must seed a value into (e.g. via `gcloud secrets versions add`) before the control plane can read AUDIT_LOG_SIGNING_KEY."
  value       = google_secret_manager_secret.audit_log_signing_key.secret_id
}
