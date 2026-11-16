#!/usr/bin/env bash
# Shared helpers for the Tiny Shenzhen Cloudflare deploy scripts.
# Never print, echo, log or write CLOUDFLARE_API_TOKEN, R2 keys, or derived secrets.

# A caller that enabled xtrace would leak tokens in the log. Turn it off first.
set +x
set -euo pipefail

CF_API="https://api.cloudflare.com/client/v4"
: "${CLOUDFLARE_ACCOUNT_ID:=3be008b5021a0d66c80ab45c07302318}"
: "${R2_BUCKET:=tinyshenzhen-data}"
: "${SITE_DOMAIN:=tinysz.com}"
: "${DATA_HOST:=data.tinysz.com}"
: "${PAGES_PROJECT:=tinyshenzhen}"
: "${ZONE_NAME:=tinysz.com}"

# Set by cf_req: the last HTTP status code.
CF_HTTP_CODE=0

_cf_auth_file=""
_cf_exit_hooks=()

# Scripts register cleanup with cf_on_exit so they share one EXIT trap.
# (A second `trap ... EXIT` would otherwise drop the auth-file cleanup.)
_cf_run_exit_hooks() {
  local rc=$?
  local i
  for ((i = ${#_cf_exit_hooks[@]} - 1; i >= 0; i--)); do
    "${_cf_exit_hooks[i]}" || true
  done
  return "${rc}"
}
trap _cf_run_exit_hooks EXIT

cf_on_exit() {
  _cf_exit_hooks+=("$1")
}

_cf_cleanup_auth() {
  if [[ -n "${_cf_auth_file}" && -f "${_cf_auth_file}" ]]; then
    rm -f "${_cf_auth_file}"
  fi
  _cf_auth_file=""
}

cf_require_token() {
  if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
    echo "CLOUDFLARE_API_TOKEN is not set." >&2
    exit 1
  fi
}

# Write the Authorization header to a 0600 temp file so the token is not in argv.
cf_auth_file() {
  cf_require_token
  if [[ -z "${_cf_auth_file}" ]]; then
    _cf_auth_file="$(mktemp)"
    chmod 600 "${_cf_auth_file}"
    printf 'Authorization: Bearer %s\n' "${CLOUDFLARE_API_TOKEN}" > "${_cf_auth_file}"
    cf_on_exit _cf_cleanup_auth
  fi
  printf '%s' "${_cf_auth_file}"
}

# cf_req METHOD PATH [JSON_BODY]
# Sets CF_RESPONSE and CF_HTTP_CODE in the current shell. Do not call it inside
# $() — that would lose the status code in a subshell.
CF_RESPONSE=""
cf_req() {
  local method="$1" path="$2" body="${3-}"
  local out
  out="$(mktemp)"
  local args=(-sS -o "${out}" -w '%{http_code}' -X "${method}"
    -H "@$(cf_auth_file)"
    -H "Content-Type: application/json"
    "${CF_API}${path}")
  if [[ -n "${body}" ]]; then
    args+=(--data-binary "${body}")
  fi
  # The token lives in the header file, not in argv.
  if ! CF_HTTP_CODE="$(curl "${args[@]}")"; then
    rm -f "${out}"
    CF_RESPONSE=""
    echo "curl failed talking to the Cloudflare API (${method} ${path})." >&2
    return 1
  fi
  CF_RESPONSE="$(cat "${out}")"
  rm -f "${out}"
}

# Back-compat wrapper. Prefer cf_req in scripts so CF_HTTP_CODE is not lost.
cf_api() {
  cf_req "$@"
  printf '%s' "${CF_RESPONSE}"
}

cf_success() {
  local body="$1"
  [[ "${CF_HTTP_CODE}" -ge 200 && "${CF_HTTP_CODE}" -lt 300 ]] \
    && [[ "$(printf '%s' "${body}" | jq -r '.success // false')" == "true" ]]
}

cf_error_code() {
  printf '%s' "$1" | jq -r '.errors[0].code // empty'
}

cf_error_message() {
  printf '%s' "$1" | jq -r '.errors[0].message // empty'
}

# Print a compact error (never a raw token) and return 1.
cf_fail() {
  local context="$1" body="${2-}"
  echo "${context} (HTTP ${CF_HTTP_CODE})." >&2
  if [[ -n "${body}" ]]; then
    printf '%s\n' "${body}" | jq -c '{success, errors}' 2>/dev/null >&2 \
      || echo "(non-JSON error body omitted)" >&2
  fi
  local code
  code="$(cf_error_code "${body}")"
  if [[ "${code}" == "10042" ]]; then
    echo "R2 is not enabled on this account yet. Turn it on in the Cloudflare dashboard, then rerun." >&2
  fi
  return 1
}

# Export rclone / AWS-style env for the R2 S3 endpoint.
# Prefers R2_ACCESS_KEY_ID + R2_SECRET_ACCESS_KEY when both are set.
# Otherwise derives them in-process from CLOUDFLARE_API_TOKEN:
#   access key id = token id (GET /accounts/:id/tokens/verify)
#   secret        = sha256 hex of the token value
# Never prints the values.
r2_export_s3_env() {
  local account="${CLOUDFLARE_ACCOUNT_ID}"
  if [[ -n "${R2_ACCESS_KEY_ID:-}" && -n "${R2_SECRET_ACCESS_KEY:-}" ]]; then
    export AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}"
    export AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}"
  else
    cf_require_token
    local id secret
    cf_req GET "/accounts/${account}/tokens/verify"
    if ! cf_success "${CF_RESPONSE}"; then
      cf_fail "Could not verify CLOUDFLARE_API_TOKEN at /accounts/${account}/tokens/verify" "${CF_RESPONSE}"
      return 1
    fi
    id="$(printf '%s' "${CF_RESPONSE}" | jq -r '.result.id // empty')"
    if [[ -z "${id}" ]]; then
      echo "Token verify returned no id; cannot derive R2 S3 credentials." >&2
      return 1
    fi
    secret="$(printf '%s' "${CLOUDFLARE_API_TOKEN}" | sha256sum | awk '{print $1}')"
    export AWS_ACCESS_KEY_ID="${id}"
    export AWS_SECRET_ACCESS_KEY="${secret}"
  fi
  export AWS_REGION="${R2_REGION:-auto}"
  export AWS_ENDPOINT_URL="${R2_ENDPOINT:-https://${account}.r2.cloudflarestorage.com}"
  # rclone remote named "r2" configured entirely from the environment. No config file.
  export RCLONE_CONFIG_R2_TYPE=s3
  export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
  export RCLONE_CONFIG_R2_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID}"
  export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY}"
  export RCLONE_CONFIG_R2_ENDPOINT="${AWS_ENDPOINT_URL}"
  export RCLONE_CONFIG_R2_REGION="${AWS_REGION}"
  export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
}

# Install rclone into ~/.local/bin if it is not already on PATH.
ensure_rclone() {
  if command -v rclone >/dev/null 2>&1; then
    return 0
  fi
  local bin_dir="${HOME}/.local/bin"
  mkdir -p "${bin_dir}"
  local tmp arch zip
  tmp="$(mktemp -d)"
  arch="$(uname -m)"
  case "${arch}" in
    x86_64|amd64) zip="rclone-current-linux-amd64.zip" ;;
    aarch64|arm64) zip="rclone-current-linux-arm64.zip" ;;
    *)
      echo "No rclone build mapped for architecture ${arch}." >&2
      rm -rf "${tmp}"
      return 1
      ;;
  esac
  echo "Installing rclone to ${bin_dir} ..."
  curl -fsSL "https://downloads.rclone.org/${zip}" -o "${tmp}/rclone.zip"
  unzip -q "${tmp}/rclone.zip" -d "${tmp}"
  local src
  src="$(find "${tmp}" -type f -name rclone -perm -u+x | head -n 1)"
  if [[ -z "${src}" ]]; then
    echo "rclone binary missing from the downloaded archive." >&2
    rm -rf "${tmp}"
    return 1
  fi
  install -m 755 "${src}" "${bin_dir}/rclone"
  rm -rf "${tmp}"
  export PATH="${bin_dir}:${PATH}"
  command -v rclone >/dev/null 2>&1
}

repo_root() {
  local dir
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  printf '%s' "${dir}"
}

