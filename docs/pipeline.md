# Restyle pipeline

The procedural renderer draws the whole map on its own. This page covers the
optional restyle: redrawing the procedural render in the Pixel-Singapore-inspired
look with an image model, checking the result against real land and water, and
slicing it into the viewer. The live `tinysz-core` map was made this way.

```text
render_full.mjs ──► restyle_map.py ──► water_constrain.py ──► slice_frontend.py ──► viewer
 procedural chunks   image-model windows   undo invented water    512-px tile pyramid
                                                                  + free polish passes
```

Only `restyle_map.py` calls an image model. Everything after it runs locally.

## Before you start

- Node 22+ and Python 3 with Pillow and NumPy.
- Build the pipeline once: `cd tinyworlds-pipeline && npm install && npx tsc -p tsconfig.json`.
- `OPENAI_API_KEY` in the environment for the restyle step. Never print it or
  write it to a file.
- Run the commands below from the repository root.
- The restyle sends only our own procedural renders. Never send third-party map
  images (Pixel Singapore or any other) to the API or use them for training; the
  look is described in words.

## 1. Render procedural chunks

```bash
node --max-old-space-size=4096 lora/render_full.mjs --city tinysz --out /tmp/tinysz-futian
node --max-old-space-size=4096 lora/render_full.mjs --city tinysz-nanshan --out /tmp/tinysz-nanshan
node --max-old-space-size=4096 lora/render_full.mjs --city tinysz-luohu --out /tmp/tinysz-luohu
node --max-old-space-size=4096 lora/render_full.mjs --city tinysz-core --out /tmp/tinysz-core
```

`tinysz-core` is only needed for its grid and geo origin when slicing; the
three districts are what get restyled.

## 2. Restyle

`restyle_map.py` cuts a render into overlapping 1024-px windows with a 256-px
hem, restyles each window, and blends them back with a smooth-step hem.

```bash
for name in futian nanshan luohu; do
  python3 lora/restyle_map.py \
    --chunks "/tmp/tinysz-$name" --out "/tmp/tinysz-$name-restyled" \
    --preview "/tmp/tinysz-$name-restyled-preview.jpg" \
    --full "/tmp/tinysz-$name-restyled.png" \
    --quality medium
done
```

Set `--preview` and `--full` explicitly: their defaults point at the Futian
box.

Useful flags:

| Flag | What it does |
| --- | --- |
| `--quality low\|medium\|high` | Passed to the Images API (default `high`). Nanshan and Luohu used `medium`. |
| `--count-only` | Counts the windows that would be sent. Smooth fog windows are skipped and never sent. No API calls. |
| `--shard I/N` | Restyles a disjoint slice (0-based). Shards can run in parallel against one `--out`. |
| `--stitch-only` | Stitches already-restyled windows, for example after sharding. |
| `--windows x,y;x,y` | Restyles an explicit list of window origins. |
| `--limit N` | Restyles at most N new windows, for a trial run. |
| `--concurrency N` | Parallel requests per process. |

The run checkpoints every window, so re-running the same command resumes it.
Don't restyle `tinysz-full` this way: it is thousands of windows.

## 3. Constrain land and water

The image model sometimes invents rivers on pale land or paves over real water.
`render_water_mask.mjs` rasterizes the scene's real water, and
`water_constrain.py` composites the procedural pixels back (feathered and
tone-matched) wherever the restyle disagrees. It also puts invented rooftop
gardens back to the procedural roof. Glass is left alone; it is already teal in
the procedural render.

```bash
for d in futian:tinysz nanshan:tinysz-nanshan luohu:tinysz-luohu; do
  name=${d%%:*} city=${d#*:}
  node --max-old-space-size=4096 lora/render_water_mask.mjs \
    --city "$city" --out "/tmp/tinysz-$name/water_mask.png"
  python3 lora/water_constrain.py \
    --chunks "/tmp/tinysz-$name" \
    --restyled "/tmp/tinysz-$name-restyled" \
    --mask "/tmp/tinysz-$name/water_mask.png" \
    --out-tiles "/tmp/tinysz-$name-restyled/tiles-constrained" \
    --preview "/tmp/tinysz-$name-constrained-preview.jpg" \
    --full "/tmp/tinysz-$name-constrained.png"
done
```

## 4. Slice into the viewer

For the three-district map, slice tile by tile from each district's constrained
windows. Each district is reprojected from its own geo origin into the core
world's origin, so a full-size canvas is never decoded.

```bash
CORE=(--chunks /tmp/tinysz-core
      --out tinyworlds-frontend/public/data/tinysz-core --name tinysz-core
      --district /tmp/tinysz-nanshan:/tmp/tinysz-nanshan-restyled/tiles-constrained
      --district /tmp/tinysz-futian:/tmp/tinysz-futian-restyled/tiles-constrained
      --district /tmp/tinysz-luohu:/tmp/tinysz-luohu-restyled/tiles-constrained)

python3 lora/slice_frontend.py "${CORE[@]}" --tilewise
```

Then clean up the map edge and seams with the polish passes, in this order
(see [polish-passes.md](polish-passes.md)):

```bash
python3 lora/slice_frontend.py "${CORE[@]}" --polish-vignette
python3 lora/slice_frontend.py "${CORE[@]}" --polish-water --polish-forest --polish-flats
python3 lora/slice_frontend.py "${CORE[@]}" --polish-fogline
python3 lora/slice_frontend.py "${CORE[@]}" --polish-mint --polish-spots lora/spots/tinysz-core.json
python3 lora/slice_frontend.py "${CORE[@]}" --polish-tone   # supersedes --polish-seams
python3 lora/slice_frontend.py "${CORE[@]}" --polish-gaps --gaps-backup /workspace/tinysz-core-z0-backup-gaps
python3 lora/slice_frontend.py "${CORE[@]}" --polish-coverage /tmp/coverage.geojson --coverage-backup /workspace/tinysz-core-z0-backup-coverage
```

The last step needs the coverage outline from the pipeline first:
`(cd tinyworlds-pipeline && npm run cli -- --city tinysz-core coverage --out /tmp/coverage.geojson)`.

With the same outline, refresh the viewer's cover mask (the night lights stay inside it):

```bash
(cd lora && python3 cover_mask.py --outline /tmp/coverage.geojson \
  --manifest ../tinyworlds-frontend/public/data/tinysz-core/manifest.json \
  --out ../tinyworlds-frontend/src/worlds/tinysz-core.cover.ts)
```

Then build the compressed web pyramid the live site serves (free, idempotent,
needs libwebp's `cwebp` on `PATH` or in `CWEBP`):

```bash
python3 lora/slice_frontend.py "${CORE[@]}" --web-tiles   # or: python3 lora/web_tiles.py --out <world dir>
```

The polish passes write lossless z0 tiles to `tiles-restyled/` (about 250 KB
each, 1.4 GB for the core map). `--web-tiles` never touches them. It writes
`tiles-web/` next to them: z0 re-encoded with cwebp `-q 80 -m 6 -sharp_yuv`
(about 61 KB each, 347 MB), with near-lossless 60 for tiles of fine checker
dither, where lossy 4:2:0 smears the colours (PSNR under 28 dB), and the
original bytes for any tile that would not get smaller; z1 and up, already
lossy and small, are hard-linked. The manifest's `restyle` becomes
`{"dir": "tiles-web", "encoding": {...the parameters..., "source": "tiles-restyled"}}`
and `build` is bumped. The parameters live in `lora/web_tiles.py`; changing
them re-encodes every tile on the next run. A rerun only redoes tiles whose
source is newer, and removes tiles that left the source.

Every other `slice_frontend.py` run rewrites the manifest back to
`tiles-restyled`, so run `--web-tiles` again as the last step after any polish.

Finally, index the world so search and the restyle field stay on the manifest,
and open the viewer:

```bash
(cd tinyworlds-pipeline && npm run cli -- --city tinysz-core index)
(cd tinyworlds-frontend && NEXT_PUBLIC_WORLD=tinysz-core npm run dev)
```

When a world has both pyramids (`tiles-restyled` and `tiles`), Settings can
switch between the restyled and procedural layers.

### Single-image slicing

A small world such as the Futian test box can also be sliced from one stitched
image:

```bash
python3 lora/slice_frontend.py \
  --image /tmp/tinysz-futian-restyled.png --chunks /tmp/tinysz-futian \
  --out tinyworlds-frontend/public/data/tinysz \
  --restyle-dir tiles-restyled --procedural-dir tiles
```

## One-off restyles

The pipeline CLI restyles a single image, which is handy for trying the look on
a preview:

```bash
cd tinyworlds-pipeline
npm run cli -- --city tinysz preview --at 114.0683,22.5455
npm run cli -- restyle --in preview.png --out out.png --look pixelsg --quality medium
```

## What is committed

Nothing generated is committed: raw extracts (`tinyworlds-pipeline/data/`),
renders, restyled windows and tiles (`tinyworlds-frontend/public/data/`) are
all reproducible with the commands above. The one exception is the README
screenshot in `docs/images/`.
