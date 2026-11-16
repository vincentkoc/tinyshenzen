#!/usr/bin/env bash
# Upload public/data/<world>/ to the tinyshenzhen-data R2 bucket under <world>/.
# Hidden files and directories (tiles-restyled/.tone-src, .seams-src, dotfiles)
# are skipped. manifest.json is uploaded last. Safe to rerun: files with the
# same size and etag/checksum are skipped.
#
# Usage: deploy/upload-data.sh [--dry-run] [--force] <world>
#
# Credentials: CLOUDFLARE_API_TOKEN (S3 keys are derived in-process) or
# R2_ACCESS_KEY_ID + R2_SECRET_ACCESS_KEY. Never prints secrets.

set +x
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'USAGE'
Usage: deploy/upload-data.sh [--dry-run] [--force] <world>

  Uploads tinyworlds-frontend/public/data/<world>/ to
  r2://$R2_BUCKET/<world>/ (default bucket: tinyshenzhen-data).

  --dry-run   List file counts and bytes only. Does not talk to R2.
  --force     Re-upload even when size and etag already match.

Hidden files and directories are excluded. manifest.json goes last, with
Cache-Control: public, max-age=60. Everything else is immutable.
USAGE
}

DRY_RUN=0
FORCE=0
WORLD=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "${WORLD}" ]]; then
        echo "Unexpected argument: $1" >&2
        usage >&2
        exit 2
      fi
      WORLD="$1"
      ;;
  esac
  shift
done

if [[ -z "${WORLD}" ]]; then
  echo "Missing <world> (for example tinysz-core)." >&2
  usage >&2
  exit 2
fi

ROOT="$(repo_root)"
SRC="${ROOT}/tinyworlds-frontend/public/data/${WORLD}"
DEST="r2:${R2_BUCKET}/${WORLD}"

if [[ ! -d "${SRC}" ]]; then
  echo "No data directory at ${SRC}" >&2
  exit 1
fi
if [[ ! -f "${SRC}/manifest.json" ]]; then
  echo "manifest.json is missing under ${SRC}" >&2
  exit 1
fi

# Human-readable byte count (IEC).
fmt_bytes() {
  local n="$1"
  python3 -c "n=int('${n}')
units=['B','KiB','MiB','GiB','TiB']
i=0
x=float(n)
while x>=1024 and i<len(units)-1:
    x/=1024
    i+=1
print(f'{n} bytes ({x:.3f} {units[i]})')"
}

echo "World:  ${WORLD}"
echo "Source: ${SRC}"
echo "Dest:   ${DEST}/"

# One walk in Python; a bash loop with stat(1) on ~27k files takes half a minute.
read -r COUNT BYTES MANIFEST_SEEN < <(python3 - "${SRC}" <<'PY'
import os, sys
root = sys.argv[1]
count = bytes_ = manifest = 0
for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
    rel = os.path.relpath(dirpath, root)
    parts = [] if rel == "." else rel.split(os.sep)
    if any(p.startswith(".") for p in parts):
        dirnames[:] = []
        continue
    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
    for name in filenames:
        if name.startswith("."):
            continue
        path = os.path.join(dirpath, name)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        count += 1
        bytes_ += size
        if os.path.normpath(path) == os.path.normpath(os.path.join(root, "manifest.json")):
            manifest = 1
print(count, bytes_, manifest)
PY
)

echo "Files:  ${COUNT} (hidden files and directories excluded)"
echo "Size:   $(fmt_bytes "${BYTES}")"
if [[ "${MANIFEST_SEEN}" -ne 1 ]]; then
  echo "manifest.json is missing from the upload set." >&2
  exit 1
fi

if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo "Dry run only. Nothing was uploaded."
  exit 0
fi

ensure_rclone
r2_export_s3_env

IMMUTABLE="Cache-Control: public, max-age=31536000, immutable"
MANIFEST_CC="Cache-Control: public, max-age=60"
# Flags only — the rclone subcommand (copy / copyto) is passed separately.
# rclone -vv dumps config, including keys. Never raise verbosity above info.
RCLONE_FLAGS=(--fast-list --transfers=32 --checkers=64 --stats-one-line
  --s3-no-check-bucket)

if [[ "${FORCE}" -eq 0 ]]; then
  # Skip objects whose size and etag/checksum already match.
  RCLONE_FLAGS+=(--checksum)
else
  echo "Force: re-uploading even when size and etag match."
fi

# Filter order matters: first match wins. Drop hidden paths, then keep a type.
HIDDEN_FILTERS=(--filter '- .*' --filter '- **/.*' --filter '- **/.*/**')

run_rclone() {
  rclone copy "${RCLONE_FLAGS[@]}" "$@"
}

echo "Uploading images (immutable) ..."
run_rclone "${SRC}" "${DEST}" \
  "${HIDDEN_FILTERS[@]}" \
  --filter '+ *.webp' --filter '+ **/*.webp' \
  --filter '- *' \
  --header-upload "Content-Type: image/webp" \
  --header-upload "${IMMUTABLE}"

echo "Uploading JSON except manifest.json (immutable) ..."
run_rclone "${SRC}" "${DEST}" \
  "${HIDDEN_FILTERS[@]}" \
  --filter '- manifest.json' \
  --filter '+ *.json' --filter '+ **/*.json' \
  --filter '- *' \
  --header-upload "Content-Type: application/json" \
  --header-upload "${IMMUTABLE}"

echo "Uploading manifest.json last (max-age=60) ..."
rclone copyto "${RCLONE_FLAGS[@]}" "${SRC}/manifest.json" "${DEST}/manifest.json" \
  --header-upload "Content-Type: application/json" \
  --header-upload "${MANIFEST_CC}"

echo "Upload complete: ${DEST}/"
