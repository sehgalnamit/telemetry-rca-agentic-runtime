# =============================================================================
# ILLUSTRATIVE ARCHITECTURE TEMPLATE -- NOT APPLIED TO ANY LIVE GCP PROJECT.
#
# This file (and everything under deployment/gcp/) is a template for
# architecture illustration. It has NOT been run through `terraform plan`
# or `terraform apply` against a real project in this exercise -- no GCP
# resources were provisioned. Review project IDs, service account scopes,
# ingress settings, and IAM bindings before ever applying this against a
# real project. See docs/GCP_DEPLOYMENT.md for the full write-up.
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
  }
}
