###############################################################################
# pubsub: DIS topics + a pull subscription per topic. Refine subscription
# wiring (push vs pull, ack deadlines, dead-letter policy) per consumer when
# the services are deployed. Confirm the topic set against contracts/pubsub.
###############################################################################

variable "project_id" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "topics" {
  description = "Topic short names (e.g. csv.received). Resource names are prefixed."
  type        = list(string)
}

resource "google_pubsub_topic" "this" {
  for_each = toset(var.topics)

  project = var.project_id
  # Dots are allowed in topic names; keep the contract name readable with a prefix.
  name = "${var.name_prefix}-${replace(each.value, ".", "-")}"

  labels = {
    contract = replace(each.value, ".", "_")
  }
}

# A default pull subscription per topic, so consumers have something to attach to.
resource "google_pubsub_subscription" "default" {
  for_each = google_pubsub_topic.this

  project = var.project_id
  name    = "${each.value.name}-sub"
  topic   = each.value.id

  ack_deadline_seconds       = 30
  message_retention_duration = "604800s" # 7 days
  retain_acked_messages      = false

  expiration_policy {
    ttl = "" # never expire (staging)
  }
}

output "topic_ids" {
  value = { for k, t in google_pubsub_topic.this : k => t.id }
}

output "subscription_ids" {
  value = { for k, s in google_pubsub_subscription.default : k => s.id }
}

# SHORT names (e.g. "dis-csv-received", "dis-csv-received-sub"), keyed by the contract
# short key (e.g. "csv.received"). The app passes these to topic_path()/subscription_path(),
# which build the full path themselves — so feed it .name, never .id. Wiring the app's
# topic/subscription env vars from these outputs keeps app and infra from drifting.
output "topic_names" {
  value = { for k, t in google_pubsub_topic.this : k => t.name }
}

output "subscription_names" {
  value = { for k, s in google_pubsub_subscription.default : k => s.name }
}
