#!/usr/bin/env bash
# Idempotent one-time Cloudflare setup for Tiny Shenzhen:
#   - R2 bucket tinyshenzhen-data
#   - CORS for the site, localhost, and the Pages *.pages.dev hosts
#   - custom domain data.tinysz.com on the bucket
#   - Pages project tinyshenzhen (direct upload, no git)
#   - custom domains tinysz.com and www.tinysz.com
#   - 301 from www.tinysz.com to https://tinysz.com
#
# Usage: deploy/setup-cloudflare.sh
# Needs CLOUDFLARE_API_TOKEN (R2 Edit, Pages Edit, DNS Edit). Never prints it.

set +x
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

cf_require_token
ACCOUNT="${CLOUDFLARE_ACCOUNT_ID}"
BUCKET="${R2_BUCKET}"
PROJECT="${PAGES_PROJECT}"

echo "Account: ${ACCOUNT}"
echo "Bucket:  ${BUCKET}"
echo "Pages:   ${PROJECT}"
echo "Site:    https://${SITE_DOMAIN}  (www redirects to apex)"
echo "Data:    https://${DATA_HOST}/${WORLD:-tinysz-core}/"

zone_id() {
  cf_req GET "/zones?name=${ZONE_NAME}&account.id=${ACCOUNT}"
  if ! cf_success "${CF_RESPONSE}"; then
    cf_fail "Could not look up zone ${ZONE_NAME}" "${CF_RESPONSE}"
    return 1
  fi
  local id
  id="$(printf '%s' "${CF_RESPONSE}" | jq -r '.result[0].id // empty')"
  if [[ -z "${id}" ]]; then
    echo "Zone ${ZONE_NAME} was not found on account ${ACCOUNT}." >&2
    return 1
  fi
  printf '%s' "${id}"
}

ensure_bucket() {
  cf_req GET "/accounts/${ACCOUNT}/r2/buckets/${BUCKET}"
  if cf_success "${CF_RESPONSE}"; then
    echo "Bucket ${BUCKET} already exists."
    return 0
  fi
  local code
  code="$(cf_error_code "${CF_RESPONSE}")"
  if [[ "${code}" == "10042" ]]; then
    cf_fail "Cannot create R2 bucket ${BUCKET}" "${CF_RESPONSE}"
    return 1
  fi
  # 404 / not found -> create. Any other error is fatal.
  if [[ "${CF_HTTP_CODE}" -ne 404 && "${code}" != "10007" ]]; then
    cf_fail "Could not read R2 bucket ${BUCKET}" "${CF_RESPONSE}"
    return 1
  fi
  echo "Creating R2 bucket ${BUCKET} ..."
  cf_req POST "/accounts/${ACCOUNT}/r2/buckets" "{\"name\":\"${BUCKET}\"}"
  if ! cf_success "${CF_RESPONSE}"; then
    # Already exists is fine (race or API quirk).
    if [[ "$(cf_error_code "${CF_RESPONSE}")" == "10004" || "${CF_HTTP_CODE}" -eq 409 ]]; then
      echo "Bucket ${BUCKET} already exists."
      return 0
    fi
    cf_fail "Could not create R2 bucket ${BUCKET}" "${CF_RESPONSE}"
    return 1
  fi
  echo "Created bucket ${BUCKET}."
}

# Returns the Pages project JSON on stdout (creates it if needed).
# Fills PAGES_PROJECT_JSON. Must not run in a subshell (CF_HTTP_CODE lives here).
ensure_pages_project() {
  cf_req GET "/accounts/${ACCOUNT}/pages/projects/${PROJECT}"
  if cf_success "${CF_RESPONSE}"; then
    echo "Pages project ${PROJECT} already exists."
    PAGES_PROJECT_JSON="${CF_RESPONSE}"
    return 0
  fi
  if [[ "${CF_HTTP_CODE}" -ne 404 ]]; then
    cf_fail "Could not read Pages project ${PROJECT}" "${CF_RESPONSE}"
    return 1
  fi
  echo "Creating Pages project ${PROJECT} (direct upload, production branch main) ..."
  # No git source: this is a direct-upload project.
  cf_req POST "/accounts/${ACCOUNT}/pages/projects" \
    "{\"name\":\"${PROJECT}\",\"production_branch\":\"main\"}"
  if ! cf_success "${CF_RESPONSE}"; then
    # 8000007 = project name already used on this account.
    if [[ "$(cf_error_code "${CF_RESPONSE}")" == "8000007" ]]; then
      cf_req GET "/accounts/${ACCOUNT}/pages/projects/${PROJECT}"
      if cf_success "${CF_RESPONSE}"; then
        echo "Pages project ${PROJECT} already exists."
        PAGES_PROJECT_JSON="${CF_RESPONSE}"
        return 0
      fi
    fi
    cf_fail "Could not create Pages project ${PROJECT}" "${CF_RESPONSE}"
    return 1
  fi
  echo "Created Pages project ${PROJECT}."
  PAGES_PROJECT_JSON="${CF_RESPONSE}"
}

pages_subdomain() {
  # Cloudflare may suffix the name if tinyshenzhen.pages.dev is taken.
  printf '%s' "$1" | jq -r '.result.subdomain // empty'
}

set_cors() {
  local pages_host="$1"
  local origins payload
  origins="$(jq -nc \
    --arg site "https://${SITE_DOMAIN}" \
    --arg www "https://www.${SITE_DOMAIN}" \
    --arg local "http://localhost:3000" \
    --arg pages "https://${pages_host}" \
    --arg previews "https://*.${pages_host}" \
    '[$site, $www, $local, $pages, $previews]')"
  payload="$(jq -nc --argjson origins "${origins}" '{
    rules: [
      {
        id: "tinyshenzhen-viewer",
        allowed: {
          methods: ["GET", "HEAD"],
          origins: $origins,
          headers: ["Content-Type", "Cache-Control", "Pragma", "Range"]
        },
        exposeHeaders: ["ETag", "Content-Type", "Content-Length", "Accept-Ranges"],
        maxAgeSeconds: 86400
      }
    ]
  }')"
  echo "Setting CORS on ${BUCKET} for $(printf '%s' "${origins}" | jq -r 'join(", ")') ..."
  cf_req PUT "/accounts/${ACCOUNT}/r2/buckets/${BUCKET}/cors" "${payload}"
  if ! cf_success "${CF_RESPONSE}"; then
    cf_fail "Could not set CORS on ${BUCKET}" "${CF_RESPONSE}"
    return 1
  fi
  echo "CORS updated."
}

ensure_r2_custom_domain() {
  local zid="$1"
  cf_req GET "/accounts/${ACCOUNT}/r2/buckets/${BUCKET}/domains/custom"
  if cf_success "${CF_RESPONSE}"; then
    local found
    found="$(printf '%s' "${CF_RESPONSE}" | jq -r --arg d "${DATA_HOST}" \
      '.result.domains[]? | select(.domain == $d) | .domain')"
    if [[ "${found}" == "${DATA_HOST}" ]]; then
      echo "R2 custom domain ${DATA_HOST} is already attached."
      return 0
    fi
  elif [[ "${CF_HTTP_CODE}" -ne 404 ]]; then
    cf_fail "Could not list R2 custom domains on ${BUCKET}" "${CF_RESPONSE}"
    return 1
  fi
  echo "Attaching R2 custom domain ${DATA_HOST} ..."
  cf_req POST "/accounts/${ACCOUNT}/r2/buckets/${BUCKET}/domains/custom" \
    "$(jq -nc --arg domain "${DATA_HOST}" --arg zoneId "${zid}" \
      '{domain: $domain, zoneId: $zoneId, enabled: true}')"
  if ! cf_success "${CF_RESPONSE}"; then
    local msg
    msg="$(cf_error_message "${CF_RESPONSE}")"
    if grep -qiE 'already|exists|duplicate' <<<"${msg}"; then
      echo "R2 custom domain ${DATA_HOST} is already attached."
      return 0
    fi
    cf_fail "Could not attach ${DATA_HOST} to ${BUCKET}" "${CF_RESPONSE}"
    return 1
  fi
  echo "Attached ${DATA_HOST} to ${BUCKET}."
}

ensure_pages_domain() {
  local domain="$1"
  cf_req GET "/accounts/${ACCOUNT}/pages/projects/${PROJECT}/domains/${domain}"
  if cf_success "${CF_RESPONSE}"; then
    echo "Pages domain ${domain} is already attached."
    return 0
  fi
  echo "Attaching Pages domain ${domain} ..."
  cf_req POST "/accounts/${ACCOUNT}/pages/projects/${PROJECT}/domains" \
    "$(jq -nc --arg name "${domain}" '{name: $name}')"
  if ! cf_success "${CF_RESPONSE}"; then
    local msg
    msg="$(cf_error_message "${CF_RESPONSE}")"
    if grep -qiE 'already|exists|duplicate' <<<"${msg}"; then
      echo "Pages domain ${domain} is already attached."
      return 0
    fi
    cf_fail "Could not attach Pages domain ${domain}" "${CF_RESPONSE}"
    return 1
  fi
  echo "Attached ${domain} to ${PROJECT}."
}

# 301 www.tinysz.com -> https://tinysz.com, preserving path and query.
# Uses a zone Single Redirect so both hostnames can stay on the Pages project.
ensure_www_redirect() {
  local zid="$1"
  local description="Redirect www.${SITE_DOMAIN} to ${SITE_DOMAIN}"
  local our_rule existing
  our_rule="$(jq -nc --arg desc "${description}" --arg host "www.${SITE_DOMAIN}" --arg apex "${SITE_DOMAIN}" '{
    description: $desc,
    expression: ("http.host eq \"" + $host + "\""),
    action: "redirect",
    enabled: true,
    action_parameters: {
      from_value: {
        status_code: 301,
        target_url: { expression: ("concat(\"https://" + $apex + "\", http.request.uri.path)") },
        preserve_query_string: true
      }
    }
  }')"

  cf_req GET "/zones/${zid}/rulesets/phases/http_request_dynamic_redirect/entrypoint"
  existing="${CF_RESPONSE}"
  local existing_code="${CF_HTTP_CODE}"
  if cf_success "${existing}"; then
    local already
    already="$(printf '%s' "${existing}" | jq -r --arg d "${description}" \
      '.result.rules[]? | select(.description == $d) | .description')"
    if [[ "${already}" == "${description}" ]]; then
      echo "www → apex redirect rule already exists."
      return 0
    fi
    local ruleset_id payload
    ruleset_id="$(printf '%s' "${existing}" | jq -r '.result.id')"
    payload="$(printf '%s' "${existing}" | jq --argjson rule "${our_rule}" \
      '{rules: ((.result.rules // []) + [$rule])}')"
    echo "Adding www → apex Single Redirect ..."
    cf_req PUT "/zones/${zid}/rulesets/${ruleset_id}" "${payload}"
    if ! cf_success "${CF_RESPONSE}"; then
      echo "Warning: could not add the www → apex redirect. Both hostnames still serve the site." >&2
      printf '%s\n' "${CF_RESPONSE}" | jq -c '{success, errors}' 2>/dev/null >&2 || true
      return 0
    fi
    echo "Added www → apex redirect."
    return 0
  fi
  # existing_code kept in case we want to distinguish 404 from other errors later.
  : "${existing_code}"

  echo "Creating www → apex Single Redirect ruleset ..."
  cf_req POST "/zones/${zid}/rulesets" \
    "$(jq -nc --argjson rule "${our_rule}" '{
      name: "tinysz.com single redirects",
      kind: "zone",
      phase: "http_request_dynamic_redirect",
      rules: [$rule]
    }')"
  if ! cf_success "${CF_RESPONSE}"; then
    echo "Warning: could not create the www → apex redirect. Both hostnames still serve the site." >&2
    printf '%s\n' "${CF_RESPONSE}" | jq -c '{success, errors}' 2>/dev/null >&2 || true
    return 0
  fi
  echo "Created www → apex redirect."
}

ZONE_ID="$(zone_id)"
echo "Zone:    ${ZONE_NAME} (${ZONE_ID})"

ensure_bucket

PAGES_PROJECT_JSON=""
ensure_pages_project
PAGES_HOST="$(pages_subdomain "${PAGES_PROJECT_JSON}")"
if [[ -z "${PAGES_HOST}" ]]; then
  PAGES_HOST="${PROJECT}.pages.dev"
  echo "Pages subdomain missing from the API response; falling back to ${PAGES_HOST}."
else
  echo "Pages host: https://${PAGES_HOST}"
fi

set_cors "${PAGES_HOST}"
ensure_r2_custom_domain "${ZONE_ID}"
ensure_pages_domain "${SITE_DOMAIN}"
ensure_pages_domain "www.${SITE_DOMAIN}"
ensure_www_redirect "${ZONE_ID}"

echo "Setup complete."
echo "Next: deploy/upload-data.sh tinysz-core"
echo "Then: deploy/deploy-site.sh"
