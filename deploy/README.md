# Deploy Tiny Shenzhen to Cloudflare

The site is a static export of `tinysz-core`, hosted on Cloudflare Pages at
[https://tinysz.com](https://tinysz.com). `www.tinysz.com` redirects to the apex.
Map tiles and indexes live in the `tinyshenzhen-data` R2 bucket, behind
[https://data.tinysz.com/tinysz-core/](https://data.tinysz.com/tinysz-core/).

`public/data` is gitignored on purpose and is never part of the Pages upload.
Tiles are published only from this `deploy/upload-data.sh` script.

## What you need

1. R2 turned on in the Cloudflare dashboard (it may ask for a card once; usage
   stays inside the free tier at this size).
2. The zone `tinysz.com` on the same account, which it already is.
3. `CLOUDFLARE_API_TOKEN` in the environment, with Account / Workers R2 Storage
   Edit, Account / Cloudflare Pages Edit, and Zone / DNS Edit limited to
   `tinysz.com`. The scripts never print, log or write the token.
4. Optional overrides: `CLOUDFLARE_ACCOUNT_ID` (defaults to Vincent's account),
   `R2_BUCKET`, `R2_ACCESS_KEY_ID` + `R2_SECRET_ACCESS_KEY`. If the S3 keys are
   unset, they are derived in-process from the API token (access key id = token
   id, secret = SHA-256 of the token value).

Account id: `3be008b5021a0d66c80ab45c07302318`.

## One-time setup

```bash
export CLOUDFLARE_API_TOKEN=...   # do not echo this
./deploy/setup-cloudflare.sh
```

The script is safe to rerun. It creates the bucket, sets CORS (GET and HEAD from
`https://tinysz.com`, `https://www.tinysz.com`, `http://localhost:3000`, and the
project's `*.pages.dev` hosts), attaches `data.tinysz.com` to the bucket, creates
the direct-upload Pages project `tinyshenzhen`, attaches `tinysz.com` and
`www.tinysz.com`, and adds a 301 from www to the apex.

## Upload map data

```bash
./deploy/upload-data.sh --dry-run tinysz-core   # counts only, no network
./deploy/upload-data.sh tinysz-core
```

Hidden files and directories (`.tone-src`, `.seams-src`, other polish working
trees, dotfiles) are skipped. Objects with the same size and etag are skipped,
so an interrupted run can be started again. `manifest.json` is uploaded last,
with `Cache-Control: public, max-age=60`. Everything else is immutable
(`public, max-age=31536000, immutable`). Content types are `image/webp` and
`application/json`.

Pass `--force` to re-upload even when size and etag match. rclone is installed
to `~/.local/bin` if it is not already on `PATH`.

### Web tiles and the lossless originals

The live map loads `tiles-web/`, the compressed copy built by
`python3 lora/slice_frontend.py --out <world dir> --web-tiles` (see
[docs/pipeline.md](../docs/pipeline.md)). The lossless `tiles-restyled/`
originals stay on disk and in the bucket; `upload-data.sh` uploads both, and
nothing is ever deleted from R2. For tinysz-core that is 34,755 objects: 7,666
in each pyramid, 13,120 procedural `tiles/`, and the JSON and sights.

`manifest.json` picks the pyramid:

```json
"restyle": {"dir": "tiles-web", "encoding": {"quality": 80, "method": 6, "sharp_yuv": true, "source": "tiles-restyled", ...}}
```

To switch the live map back to the originals, set `"dir"` to
`"tiles-restyled"` (the `encoding.source` value), give `build` a new value
(any new 9-hex-digit string, so `?v=` busts the tile caches), and run
`./deploy/upload-data.sh tinysz-core`. Only the manifest changes; it is cached
for 60 seconds. Switch forward again by rerunning `--web-tiles`.

## Deploy the site locally

```bash
export CLOUDFLARE_API_TOKEN=...
./deploy/deploy-site.sh
```

This moves `tinyworlds-frontend/public/data` aside (so `out/` does not contain
several gigabytes of tiles), builds with:

```text
NEXT_PUBLIC_WORLD=tinysz-core
NEXT_PUBLIC_DATA_BASE=https://data.tinysz.com/tinysz-core
NEXT_PUBLIC_SITE_URL=https://tinysz.com
```

then deploys `out/` with `npx wrangler pages deploy` to the `tinyshenzhen`
project. `public/data` is restored even if the build or deploy fails.
`--build-only` stops after the build.

Do this after the first data upload, or the site will come up with a map that
cannot load tiles.

## GitHub Actions

[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) deploys the
frontend on a push to `main` that touches `tinyworlds-frontend/**` or the
workflow file, and on `workflow_dispatch`. It installs dependencies, runs the
frontend tests, builds with the same `NEXT_PUBLIC_*` values as
`deploy-site.sh`, and deploys `out/` to the `tinyshenzhen` Pages project via
`cloudflare/wrangler-action`.

Add these repository secrets before the first run (Settings → Secrets and
variables → Actions). The workflow does not create them:

- `CLOUDFLARE_API_TOKEN` — same permissions as the local scripts (Pages Edit is
  enough for the site deploy; R2 is not used in CI)
- `CLOUDFLARE_ACCOUNT_ID` — `3be008b5021a0d66c80ab45c07302318`

Actions also has to be enabled for the repository. Tile upload stays local:
map data is not in git, so `./deploy/upload-data.sh tinysz-core` is still
required whenever the tiles or indexes change.

## Suggested order for a first publish

1. Turn R2 on in the dashboard.
2. `./deploy/setup-cloudflare.sh`
3. `python3 lora/slice_frontend.py --out tinyworlds-frontend/public/data/tinysz-core --web-tiles`,
   then `./deploy/upload-data.sh tinysz-core`
4. Either `./deploy/deploy-site.sh`, or add the two repo secrets and push to
   `main` (or run the workflow by hand).

After that, frontend-only changes can go out through Actions. New tiles still
need a local `upload-data.sh` run, with `manifest.json` landing last.
