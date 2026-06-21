output "cloud_sql_connection_name" {
  description = "Use this for Alembic + the Cloud SQL connector (project:region:instance)."
  value       = module.cloud_sql.connection_name
}

output "cloud_sql_private_ip" {
  value = module.cloud_sql.private_ip
}

output "bucket_names" {
  value = module.buckets.names
}

output "pubsub_topic_ids" {
  value = module.pubsub.topic_ids
}

output "artifact_repo_url" {
  description = "Push images here."
  value       = module.artifact_registry.repo_url
}

output "service_account_emails" {
  value = module.service_accounts.emails
}

output "dis_ui_server_url" {
  value       = length(module.dis_ui_server) > 0 ? module.dis_ui_server[0].url : ""
  description = "Set this back into var.dis_ui_server_url so the frontend can target it."
}

output "dis_ui_url" {
  value = length(module.dis_ui) > 0 ? module.dis_ui[0].url : ""
}

output "sftpgo_lb_ip" {
  value = module.sftpgo.lb_ip
}
