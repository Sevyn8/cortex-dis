#!/usr/bin/env bash
# Generate 90-day-expiry DIS dev JWTs and save each to its own file.
#
# Usage:
#   ./scripts/jwt/gen_dis_jwt_90d.sh                      # mint all 10 selected users
#   ./scripts/jwt/gen_dis_jwt_90d.sh marcus.t@bucees.com  # mint just one
#
# Output: scripts/jwt/tokens/<email-prefix>-90d.jwt  (one file per user)
#
# These are DEV-STUB tokens: HS256-signed with the constant secret the
# dis-ui-server verifier checks (services/dis-ui-server/src/dis_ui_server/auth/
# verifier.py). NOT production credentials. The 13b JWKS/RS256 swap (D25)
# replaces the verifier; these tokens stop working then.
#
# Unlike the admin-backend generate_7d.sh, this does NO database lookup. The 10
# users (sub = auth0_sub, tenant_id = CLOUD core.tenants.id) are pinned inline,
# precisely to avoid the local-vs-cloud tenant_id mismatch: a JWT must carry the
# CLOUD tenant UUID to see cloud rows. These are the cloud Buc-ee's / Zabka UUIDs.
#
# Roles: DIS does not discriminate by role today (every shipped endpoint is
# tenant-presence-only; dis:ops is the only role inspected and no live route is
# behind it). All tokens carry the full DIS role set; tenant isolation is
# enforced by RLS on the signed tenant_id, not by role.

set -euo pipefail

# --- dev-stub verifier constants (must match auth/verifier.py) ---
SECRET="dis-ui-dev-stub-secret-not-for-production"
ISSUER="https://customer-master.local"
AUDIENCE="dis"
TTL_DAYS=90

# Cloud tenant UUIDs (core.tenants.id on the cloud platform DB).
TENANT_BUCEES="019df261-b878-7c78-ad1c-da36f80aa17c"
TENANT_ZABKA="019df261-b87c-7d3e-ab9e-dcf26259cec6"

# Selected ACTIVE users: "email|sub|tenant_id|label"
# label is just for the console line; the filename comes from the email prefix.
USERS=(
  "marcus.t@bucees.com|auth0|b92da22b21df306f|${TENANT_BUCEES}|Bucees/Marcus Tanner (Owner)"
  "t.vaughn@bucees.com|auth0|5ab33edf6e595ed3|${TENANT_BUCEES}|Bucees/Tasha Vaughn (Store Manager)"
  "j.cole@bucees.com|auth0|7914c120c8dcd19f|${TENANT_BUCEES}|Bucees/Jamie Cole (Pricing Manager)"
  "h.ruiz@bucees.com|auth0|a748dbcfac619e63|${TENANT_BUCEES}|Bucees/Hector Ruiz (Associate)"
  "lila.h@bucees.com|auth0|a6f2f7b80cf35b58|${TENANT_BUCEES}|Bucees/Lila Hawthorne (Associate)"
  "w.ortiz@bucees.com|auth0|c0e9ab30ed2662e9|${TENANT_BUCEES}|Bucees/Wesley Ortiz (Perishables Lead)"
  "a.kowalski@zabka.pl|auth0|ccf3a17156dc8907|${TENANT_ZABKA}|Zabka/Anna Kowalski (Owner)"
  "k.wojcik@zabka.pl|auth0|dd59ba7136b82481|${TENANT_ZABKA}|Zabka/Krzysztof Wojcik (Regional Director)"
  "m.lis@zabka.pl|auth0|134c6c92ec5b227c|${TENANT_ZABKA}|Zabka/Magda Lis (Store Manager)"
  "p.nowak@zabka.pl|auth0|23e2fcb472d8567d|${TENANT_ZABKA}|Zabka/Piotr Nowak (Category Manager)"
)
# NOTE: auth0_sub literally contains a "|" (e.g. auth0|b92da...), so each line has
# 5 pipe-delimited parts: email | sub-prefix | sub-suffix | tenant_id | label.
# The sub is reassembled as "<part2>|<part3>".

# Resolve repo root from this script's location (scripts/jwt/<this>.sh -> ../..).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# Sanity: refuse to run if we did not land on the repo root (avoids writing to /
# or some unexpected dir if the script is moved). A repo marker must be present.
if [ ! -e "${REPO_ROOT}/pyproject.toml" ] && [ ! -d "${REPO_ROOT}/services" ]; then
  echo "ERROR: ${REPO_ROOT} does not look like the repo root (no pyproject.toml / services/)." >&2
  echo "Place this script at scripts/jwt/ and run it from there." >&2
  exit 1
fi
cd "$REPO_ROOT"
mkdir -p scripts/jwt/tokens

FILTER="${1:-}"   # optional email to mint just one

mint_one() {
  local email="$1" sub="$2" tenant_id="$3" label="$4"
  local jwt
  jwt=$(uv run python - "$sub" "$tenant_id" "$ISSUER" "$AUDIENCE" "$SECRET" "$TTL_DAYS" <<'PY'
import sys, datetime as dt, jwt
sub, tenant_id, issuer, audience, secret, ttl_days = sys.argv[1:7]
now = dt.datetime.now(tz=dt.timezone.utc)
payload = {
    "sub": sub,
    "iss": issuer,
    "aud": audience,
    "iat": int(now.timestamp()),
    "exp": int((now + dt.timedelta(days=int(ttl_days))).timestamp()),
    "tenant_id": tenant_id,
    "roles": ["dis:ops", "dis:read", "dis:upload", "dis:mapping_admin"],
}
print(jwt.encode(payload, secret, algorithm="HS256"))
PY
)
  local prefix
  prefix=$(echo "$email" | sed 's/@.*//' | sed 's/[^a-zA-Z0-9_-]/-/g')
  local outfile="scripts/jwt/tokens/${prefix}-dis-90d.jwt"
  printf '%s\n' "$jwt" > "$outfile"
  if [ ! -s "$outfile" ]; then
    echo "ERROR: failed to write $outfile (empty or missing)." >&2
    exit 1
  fi
  echo "Generated: $outfile  (${label}, sub=${sub}, tenant_id=${tenant_id})"
}

found=0
for entry in "${USERS[@]}"; do
  IFS='|' read -r email sub_prefix sub_suffix tenant_id label <<< "$entry"
  sub="${sub_prefix}|${sub_suffix}"
  if [ -n "$FILTER" ] && [ "$email" != "$FILTER" ]; then
    continue
  fi
  mint_one "$email" "$sub" "$tenant_id" "$label"
  found=1
done

if [ "$found" -eq 0 ]; then
  echo "No selected user with email '$FILTER'." >&2
  echo "Known emails:" >&2
  for entry in "${USERS[@]}"; do IFS='|' read -r email _ _ _ _ <<< "$entry"; echo "  $email" >&2; done
  exit 1
fi

echo
echo "Done. Tokens are 90-day, full DIS roles. Each token's signed tenant_id is the RLS boundary."
echo "Use:  curl -s <base>/api/v1/stores-onboarded -H \"Authorization: Bearer \$(cat scripts/jwt/tokens/marcus-t-dis-90d.jwt)\""
