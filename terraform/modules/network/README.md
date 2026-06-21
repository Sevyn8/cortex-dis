# Module: network

VPC, subnet (private Google access on), private service access range + connection (so Cloud SQL gets a private IP), a Serverless VPC Access connector (so Cloud Run can reach private Cloud SQL), and Cloud NAT (egress for resources with no external IP, like the SFTPGo VM). No external IPs are created here.
