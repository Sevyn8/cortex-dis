###############################################################################
# Vertex AI IAM, codifying grants applied by hand in GCP (now in Terraform).
#
# The mapping-suggester runs as dis-ui-server's own SA but IMPERSONATES the
# gemini-dis service account for the Vertex calls only (short-lived credentials).
# Two grants make that work, both ALREADY APPLIED in GCP out of band:
#
#   1. gemini-dis            -> roles/aiplatform.user      (project binding)
#   2. dis-ui-server (SA)    -> roles/iam.serviceAccountTokenCreator ON gemini-dis
#
# Both are NON-AUTHORITATIVE (google_*_iam_member): they ADD a single member and
# never rewrite the policy, so they ADOPT the existing grants rather than create
# new access. A first plan shows them as additions because they are not yet in
# state; applying is idempotent (the live grant already matches).
#
# gemini-dis is NOT managed by the service-accounts module (it was created by
# hand); it is referenced by email. gemini-dis's separately-accepted roles/owner
# is intentionally NOT touched here.
###############################################################################

locals {
  # projects/<project>/serviceAccounts/<email> is the resource id form a per-SA
  # IAM member binds against. The email is var.gemini_dis_sa_email (verify it
  # matches the hand-created SA when the image lands).
  gemini_dis_sa_id = "projects/${var.project_id}/serviceAccounts/${var.gemini_dis_sa_email}"
}

# (1) gemini-dis may call Vertex AI. Adopts the existing project grant.
resource "google_project_iam_member" "gemini_dis_aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${var.gemini_dis_sa_email}"
}

# (2) dis-ui-server's SA may mint short-lived tokens for gemini-dis (impersonation
# for the Vertex calls only). Adopts the existing per-SA grant.
resource "google_service_account_iam_member" "ui_server_impersonates_gemini_dis" {
  service_account_id = local.gemini_dis_sa_id
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${module.service_accounts.emails["ui-server"]}"
}
