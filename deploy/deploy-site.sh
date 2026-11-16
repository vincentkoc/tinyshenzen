#!/usr/bin/env bash
# Build the tinysz-core static export pointed at the R2 data host, then deploy
# out/ to the Cloudflare Pages project tinyshenzhen.
#
# public/data is moved aside for the build so the 5 GB of tiles is not copied
# into out/, and is moved back even if the build or deploy fails.
#
# Usage: deploy/deploy-site.sh [--build-only]
#
# Needs CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID for the deploy step.
# Never prints the token.

set +x
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

BUILD_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-only) BUILD_ONLY=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: deploy/deploy-site.sh [--build-only]

  Builds tinyworlds-frontend with:
    NEXT_PUBLIC_WORLD=tinysz-core
    NEXT_PUBLIC_DATA_BASE=https://data.tinysz.com/tinysz-core
  then deploys out/ to the Pages project tinyshenzhen.

  --build-only   Run the stashed build only. Do not call wrangler.
USAGE
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done

ROOT="$(repo_root)"
FRONTEND="${ROOT}/tinyworlds-frontend"
DATA_DIR="${FRONTEND}/public/data"
OUT_DIR="${FRONTEND}/out"

: "${NEXT_PUBLIC_WORLD:=tinysz-core}"
: "${NEXT_PUBLIC_DATA_BASE:=https://data.tinysz.com/tinysz-core}"
: "${NEXT_PUBLIC_SITE_URL:=https://tinysz.com}"
: "${NEXT_PUBLIC_BASE_PATH:=}"

STASH=""
DATA_WAS_STASHED=0

restore_data() {
  if [[ "${DATA_WAS_STASHED}" -eq 1 && -n "${STASH}" ]]; then
    if [[ -d "${STASH}/data" ]]; then
      # If a failed build left an empty public/data behind, drop it so the move works.
      if [[ -d "${DATA_DIR}" && -z "$(ls -A "${DATA_DIR}" 2>/dev/null || true)" ]]; then
        rmdir "${DATA_DIR}" 2>/dev/null || true
      fi
      if [[ -e "${DATA_DIR}" ]]; then
        echo "warning: ${DATA_DIR} still exists; leaving the stash at ${STASH}/data" >&2
      else
        mv "${STASH}/data" "${DATA_DIR}"
        DATA_WAS_STASHED=0
        echo "Restored public/data."
      fi
    fi
    rmdir "${STASH}" 2>/dev/null || true
  fi
}
cf_on_exit restore_data

if [[ ! -d "${FRONTEND}" ]]; then
  echo "Frontend not found at ${FRONTEND}" >&2
  exit 1
fi

if [[ -d "${DATA_DIR}" ]]; then
  STASH="$(mktemp -d "${FRONTEND}/.data-stash.XXXXXX")"
  echo "Stashing public/data at ${STASH} so out/ stays small ..."
  mv "${DATA_DIR}" "${STASH}/data"
  DATA_WAS_STASHED=1
else
  echo "public/data is already absent; building without a stash."
fi

export NEXT_PUBLIC_WORLD NEXT_PUBLIC_DATA_BASE NEXT_PUBLIC_SITE_URL NEXT_PUBLIC_BASE_PATH
# An empty base path must stay empty, not become "/".
if [[ -z "${NEXT_PUBLIC_BASE_PATH}" ]]; then
  unset NEXT_PUBLIC_BASE_PATH
fi

echo "Building ${NEXT_PUBLIC_WORLD} with DATA_BASE=${NEXT_PUBLIC_DATA_BASE} ..."
(
  cd "${FRONTEND}"
  npm run build
)

if [[ ! -d "${OUT_DIR}" ]]; then
  echo "Build finished but ${OUT_DIR} is missing." >&2
  exit 1
fi

echo "Build output: $(du -sh "${OUT_DIR}" | awk '{print $1}') in ${OUT_DIR}"

if [[ "${BUILD_ONLY}" -eq 1 ]]; then
  echo "Build-only: skipping Pages deploy."
  exit 0
fi

cf_require_token
export CLOUDFLARE_API_TOKEN
export CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID}"

echo "Deploying out/ to Pages project ${PAGES_PROJECT} ..."
# wrangler --log-level debug can dump headers. Stay on the default level.
# --commit-dirty: public/data has been moved, so the worktree looks dirty.
npx --yes wrangler pages deploy "${OUT_DIR}" \
  --project-name="${PAGES_PROJECT}" \
  --commit-dirty=true \
  --branch=main

echo "Site deploy complete: https://${SITE_DOMAIN}"
