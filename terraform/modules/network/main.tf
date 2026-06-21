###############################################################################
# network: VPC, subnet, private service access (for Cloud SQL private IP),
# a Serverless VPC Access connector (Cloud Run -> private Cloud SQL), and
# Cloud NAT (egress for resources with no external IP).
###############################################################################

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "subnet_cidr" {
  type = string
}

# --- VPC + subnet ---------------------------------------------------------

resource "google_compute_network" "vpc" {
  project                 = var.project_id
  name                    = "${var.name_prefix}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  project                  = var.project_id
  name                     = "${var.name_prefix}-subnet"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
}

# --- Private service access (so Cloud SQL gets a private IP) ---------------

resource "google_compute_global_address" "psa_range" {
  project       = var.project_id
  name          = "${var.name_prefix}-psa-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "psa" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.psa_range.name]
}

# --- Serverless VPC Access connector (Cloud Run -> private Cloud SQL) -------

resource "google_vpc_access_connector" "connector" {
  project = var.project_id
  name    = "${var.name_prefix}-vpcconn"
  region  = var.region
  network = google_compute_network.vpc.name

  # Small connector for staging.
  ip_cidr_range = "10.8.0.0/28"
  min_instances = 2
  max_instances = 3
}

# --- Cloud NAT (egress for VMs with no external IP, e.g. SFTPGo) ------------

resource "google_compute_router" "router" {
  project = var.project_id
  name    = "${var.name_prefix}-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  project                            = var.project_id
  name                               = "${var.name_prefix}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# --- Outputs ---------------------------------------------------------------

output "network_id" {
  value = google_compute_network.vpc.id
}

output "network_self_link" {
  value = google_compute_network.vpc.self_link
}

output "subnet_id" {
  value = google_compute_subnetwork.subnet.id
}

output "subnet_self_link" {
  value = google_compute_subnetwork.subnet.self_link
}

output "vpc_connector_id" {
  value = google_vpc_access_connector.connector.id
}

output "private_service_connection" {
  value = google_service_networking_connection.psa.id
}
