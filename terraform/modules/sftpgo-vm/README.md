# Module: sftpgo-vm

SFTPGo on a GCE e2-small VM (per the design; a long-lived SFTP server does not fit Cloud Run), with NO external IP (egress via Cloud NAT) and a regional TCP load balancer for inbound SFTP. The startup script installs and configures SFTPGo with the GCS backend pointed at the upload bucket and a persistent disk for its data provider. AUTH MODEL and USER PROVISIONING and HOW UPLOADS TRIGGER INGESTION are platform decisions marked for confirmation with Sanjeev.
