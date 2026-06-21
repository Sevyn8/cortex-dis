###############################################################################
# sftpgo-vm: SFTPGo on a GCE e2-small VM (per the design; a long-lived SFTP
# server does not fit Cloud Run). No external IP on the VM (egress via Cloud
# NAT); inbound SFTP via a regional TCP load balancer.
#
# This is a scaffold: the SFTPGo install/config (startup script, persistent
# disk, user/auth, bucket wiring) needs filling in with Sanjeev. The LB wiring
# below is a minimal regional passthrough sketch.
###############################################################################

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "zone" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "machine_type" {
  type = string
}

variable "network_id" {
  type = string
}

variable "subnet_id" {
  type = string
}

variable "service_account_email" {
  type = string
}

variable "upload_bucket" {
  description = "Name of the upload GCS bucket SFTPGo writes into (its GCS backend target)."
  type        = string
}

variable "data_disk_size_gb" {
  description = "Persistent disk size for the SFTPGo data provider + config."
  type        = number
  default     = 10
}

# Persistent data disk for SFTPGo config + data provider (survives VM rebuilds).
resource "google_compute_disk" "sftpgo_data" {
  project = var.project_id
  name    = "${var.name_prefix}-sftpgo-data"
  zone    = var.zone
  size    = var.data_disk_size_gb
  type    = "pd-ssd"
}

resource "google_compute_instance" "sftpgo" {
  project      = var.project_id
  name         = "${var.name_prefix}-sftpgo"
  machine_type = var.machine_type
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 20
    }
  }

  network_interface {
    network    = var.network_id
    subnetwork = var.subnet_id
    # No access_config block => no external IP (egress via Cloud NAT).
  }

  service_account {
    email  = var.service_account_email
    scopes = ["cloud-platform"]
  }

  attached_disk {
    source      = google_compute_disk.sftpgo_data.id
    device_name = "sftpgo-data"
  }

  # Installs and configures SFTPGo. The GCS backend points at the upload bucket
  # (so SFTP drops land where csv-ingest-worker expects them). The data disk
  # holds SFTPGo's config + data provider so it survives VM rebuilds.
  #
  # CONFIRM WITH SANJEEV (platform policy, not codified here):
  #   - Auth model + user provisioning (local users vs DB-backed vs keys). The
  #     block below creates NO users; provision them out of band or via the
  #     SFTPGo REST API / admin, so credentials never live in this script.
  #   - How an upload to the bucket TRIGGERS ingestion (the csv.received path):
  #     a GCS notification to Pub/Sub vs the worker watching the bucket. This
  #     module does not wire that; it is an ingestion-architecture decision.
  metadata_startup_script = <<-EOT
    #!/usr/bin/env bash
    set -euo pipefail

    UPLOAD_BUCKET="${var.upload_bucket}"
    DATA_DEV="/dev/disk/by-id/google-sftpgo-data"
    DATA_MNT="/var/lib/sftpgo"

    # Mount the persistent data disk (format on first boot only).
    if ! blkid "$${DATA_DEV}"; then
      mkfs.ext4 -m 0 -F -E lazy_itable_init=0,lazy_journal_init=0,discard "$${DATA_DEV}"
    fi
    mkdir -p "$${DATA_MNT}"
    grep -q "$${DATA_MNT}" /etc/fstab || echo "$${DATA_DEV} $${DATA_MNT} ext4 discard,defaults,nofail 0 2" >> /etc/fstab
    mount -a

    # Install SFTPGo from the official Debian package repo.
    apt-get update
    apt-get install -y curl gnupg
    curl -fsSL https://sftpgo-deb.fly.dev/sftpgo.gpg | gpg --dearmor -o /usr/share/keyrings/sftpgo.gpg
    echo "deb [signed-by=/usr/share/keyrings/sftpgo.gpg] https://sftpgo-deb.fly.dev/ /" > /etc/apt/sources.list.d/sftpgo.list
    apt-get update
    apt-get install -y sftpgo

    # Point SFTPGo's data/config at the persistent disk.
    install -d -o sftpgo -g sftpgo "$${DATA_MNT}/data" "$${DATA_MNT}/config"

    # Minimal config: SFTP on 2022, GCS backend to the upload bucket. The VM's
    # service account (cloud-platform scope) provides GCS credentials, so no
    # key file is needed. Users are NOT defined here (see CONFIRM note above).
    cat > /etc/sftpgo/sftpgo.json <<JSON
    {
      "sftpd": { "bindings": [ { "port": 2022 } ] },
      "data_provider": { "driver": "sqlite", "name": "$${DATA_MNT}/data/sftpgo.db" },
      "filesystem": { "fs_provider": 2, "gcsconfig": { "bucket": "$${UPLOAD_BUCKET}", "automatic_credentials": 1 } }
    }
    JSON

    systemctl enable sftpgo
    systemctl restart sftpgo
    echo "SFTPGo installed; SFTP on 2022; GCS backend -> $${UPLOAD_BUCKET}. Provision users out of band."
  EOT

  tags = ["${var.name_prefix}-sftpgo"]

  labels = {
    role = "sftpgo"
  }
}

# --- Regional TCP passthrough load balancer (sketch) -----------------------
# Minimal external regional passthrough NLB fronting the SFTP port. Refine the
# health check, port, and firewall with Sanjeev. Note: an external forwarding
# rule needs the org to allow external IPs on the LB front end (not the VM).

resource "google_compute_address" "sftpgo_lb" {
  project = var.project_id
  name    = "${var.name_prefix}-sftpgo-lb-ip"
  region  = var.region
}

resource "google_compute_region_health_check" "sftpgo" {
  project = var.project_id
  name    = "${var.name_prefix}-sftpgo-hc"
  region  = var.region

  tcp_health_check {
    port = 2022 # SFTPGo default SFTP port; confirm
  }
}

resource "google_compute_instance_group" "sftpgo" {
  project = var.project_id
  name    = "${var.name_prefix}-sftpgo-ig"
  zone    = var.zone

  instances = [google_compute_instance.sftpgo.self_link]

  named_port {
    name = "sftp"
    port = 2022
  }
}

resource "google_compute_region_backend_service" "sftpgo" {
  project               = var.project_id
  name                  = "${var.name_prefix}-sftpgo-bes"
  region                = var.region
  protocol              = "TCP"
  load_balancing_scheme = "EXTERNAL"
  health_checks         = [google_compute_region_health_check.sftpgo.id]

  backend {
    group = google_compute_instance_group.sftpgo.id
    # External TCP network LB backends require CONNECTION (not the default UTILIZATION).
    balancing_mode = "CONNECTION"
  }
}

resource "google_compute_forwarding_rule" "sftpgo" {
  project               = var.project_id
  name                  = "${var.name_prefix}-sftpgo-fr"
  region                = var.region
  load_balancing_scheme = "EXTERNAL"
  ip_address            = google_compute_address.sftpgo_lb.address
  ip_protocol           = "TCP"
  port_range            = "2022"
  backend_service       = google_compute_region_backend_service.sftpgo.id
}

# Firewall: allow inbound SFTP to the tagged VM, and health checks.
resource "google_compute_firewall" "sftpgo_ingress" {
  project = var.project_id
  name    = "${var.name_prefix}-sftpgo-ingress"
  network = var.network_id

  allow {
    protocol = "tcp"
    ports    = ["2022"]
  }

  # Google LB / health-check ranges + your admin source ranges. Tighten this.
  source_ranges = ["35.191.0.0/16", "130.211.0.0/22"]
  target_tags   = ["${var.name_prefix}-sftpgo"]
}

output "lb_ip" {
  value = google_compute_address.sftpgo_lb.address
}

output "instance_name" {
  value = google_compute_instance.sftpgo.name
}
