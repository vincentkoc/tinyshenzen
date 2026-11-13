# Tiny Shenzhen

An isometric pixel-art map of Shenzhen, China, in a fine naturalistic miniature style
inspired by maps such as Pixel Singapore: the Civic Center, Ping An Finance Centre and
the Futian CBD, teal glass towers, pastel mid-rises, dense urban villages, palms and the
parks, generated from open data.

This project is adapted from [nihey/tinyworlds](https://github.com/nihey/tinyworlds) (MIT
Licence, © 2026 Nihey Takizawa): the same pipeline and viewer, with a Shenzhen city
definition and a Shenzhen style. Nothing is drawn by hand. The default pipeline is procedural and needs no image model:
building footprints and heights, streets, parks, water and trees go in; a pyramid of
pixel-art tiles comes out. An optional `restyle` command can send those tiles to an image
model if you provide a key (see below).

## Cities

| Id | What it covers |
| --- | --- |
| `tinysz` (default) | A roughly two-kilometre box around the Civic Center (22.5455° N, 114.0683° E), including Ping An Finance Centre and Lianhuashan Park. Use this first: it fetches and renders quickly. |
| `tinysz-full` | The urban core: Futian, Nanshan, Luohu and neighbouring districts (lat 22.45–22.75, lon 113.80–114.30). This is a large extract; expect a long download and render. |
| `tinysz-nanshan` | Nanshan core: Shekou, Houhai, the Science Park and OCT (lon 113.90–114.048, lat 22.47–22.564). Contiguous with the Futian test box on the east. |
| `tinysz-luohu` | Luohu core: Caiwuwei, Dongmen and Luohu Port (lon 114.088–114.14, lat 22.522–22.564). Contiguous with the Futian test box on the west. |
| `tinysz-core` | The three cores as one contiguous strip (Nanshan–Futian–Luohu). |

The default style (`SHENZHEN` in `tinyworlds-pipeline/src/scene/styles.ts`) is a city-gated
Pixel-Singapore-inspired look (`look.pixelsg`): muted warm-grey and beige concrete, teal and
sky-blue glass with fine window grids, terracotta low roofs, pastel mid-rises, teal-green
water, lush rounded tree canopies and subtle (not ink) outlines. A Habbo Hotel look remains
available as `look.habbo` (with the `SHENZHEN_HABBO` materials). Other cities that share this
pipeline are untouched. Named landmarks
— Ping An Finance Centre, KK100, Window of the World, Shenzhen Bay Park, China Resources
Headquarters, Shun Hing Square, the Civic Center and the Stock Exchange — are curated so they
can be searched, and the towers get explicit materials, tapers and heights.

Data comes from [OpenStreetMap](https://www.openstreetmap.org/copyright) and the
[Overture Maps Foundation](https://overturemaps.org/) (the buildings theme already mixes OSM
with the East Asian Buildings dataset, doi:10.5281/zenodo.8174931, CC BY 4.0). Missing
footprints, and per-building heights where OSM has neither height nor floors, come from
[3D-GloBFP](https://doi.org/10.1038/s41597-024-03917-6) (Che et al., 2024, CC BY 4.0),
clipped to the city box from the Figshare tiles that cover Shenzhen. Heights that 3D-GloBFP
does not cover are read from [CNBH-10m](https://doi.org/10.5281/zenodo.7923866) (Wu et al.,
2023, CC BY 4.0), a 10 m building-height raster for China. Priority is OSM height or floors,
then 3D-GloBFP (one height per footprint), then CNBH, then a small fallback. Only 3D-GloBFP
outlines that do not overlap an existing footprint are added. Overture's own fetch still
needs no API key; 3D-GloBFP and CNBH are downloaded from Figshare and Zenodo, also publicly.
Raw tiles are cached locally and are not committed.

## Setup

Requires Node 22+ and npm. The default pipeline needs no API keys: Overture is read from a public S3 bucket. The optional `restyle` command is the exception — it reads a key from the environment if you want it.

```bash
# 1. make the map (the small Futian box; a few minutes after the download)
cd tinyworlds-pipeline
npm install
npm run all -- --city tinysz     # fetch → scene → render → index
                                 # into ../tinyworlds-frontend/public/data/tinysz

# the full urban core, when you want it
npm run all -- --city tinysz-full

# 2. look at it
cd ../tinyworlds-frontend
npm install
NEXT_PUBLIC_WORLD=tinysz-core npm run dev # http://localhost:3000 (Nanshan–Futian–Luohu)
```

Other pipeline commands, still from `tinyworlds-pipeline/`:

```bash
npm run fetch -- --city tinysz          # download only
npm run cli -- --city tinysz scene      # build the scene from already-fetched data
npm run render -- --city tinysz
npm run cli -- --city tinysz preview --at 114.0683,22.5455
npm run cli -- --city tinysz card       # share card into ../tinyworlds-frontend/public/og/
npm run cli -- restyle --in preview.png # optional image-model restyle (needs a key)
```

A Pixel-Singapore-inspired **image-edit LoRA**, trained on our own teacher
tiles (procedural windows paired with `--look pixelsg` restyles — never on a
third-party map), lives in [`lora/`](lora/README.md): dataset prep, a 24 GB
Qwen-Image-Edit-2511 training script, and MultiDiffusion inference with the
same overlapping hem as `restyle`. Teacher images and LoRA weights are not
committed.

Generated tiles and raw extracts are not committed (`tinyworlds-pipeline/data/` and
`tinyworlds-frontend/public/data/`). They are reproducible with the commands above.

The original tinyworlds city ids (New York, Las Vegas, and the rest) are still in the pipeline
if you want them; this repository is for Shenzhen.


## Optional: restyle with an image model

The map itself is still drawn by the procedural renderer. A richer isometric-pixel
pass, inspired by maps such as Pixel Singapore (the look is described in words;
we never send third-party map images to an API or use them as training data),
is in [`lora/`](lora/README.md). The Futian test box (23,500×11,175 px) is
restyled in overlapping 1024-px windows with a 256-px hem, then constrained
against the renderer's real land and water, and sliced into the viewer's tile
pyramid. The restyle is the default layer; Settings can switch back to the
procedural tiles when both pyramids are present.

From the repo root, with the pipeline built and `OPENAI_API_KEY` set (never
print it or write it to a file):

```bash
# 1. procedural chunks of the Futian test box
node --max-old-space-size=4096 lora/render_full.mjs --out /tmp/tinysz-futian

# 2. tiled OpenAI restyle (--look pixelsg). Skips smooth fog windows.
#    1024×1024 at high quality is US$0.0624 a window. Fog-skip on this box
#    sent 190 of 465 windows, about US$11.86; 40 teacher tiles were
#    US$2.50. A first attempt that invented a harbour was thrown away
#    (US$1.25). All-in, about US$15.60, including the teacher tiles.
#    Nanshan at medium quality: 941 non-fog windows, US$50.25.
#    Luohu at medium quality: 275 non-fog windows, US$14.685.
#    A short medium-vs-high sample was US$0.49. Total so far: about US$81.03.
#    Do not restyle tinysz-full this way (thousands of dollars).
python3 lora/restyle_map.py \
  --chunks /tmp/tinysz-futian \
  --out /tmp/tinysz-futian-restyled \
  --preview /tmp/tinyshenzen-futian-restyled-preview.jpg \
  --full /tmp/tinyshenzen-futian-restyled.png

# For a larger box (tinysz-nanshan, tinysz-luohu), render with
#   node --max-old-space-size=4096 lora/render_full.mjs --city tinysz-nanshan --out /tmp/tinysz-nanshan
# then restyle at --quality medium, optionally sharded across workers that share
# one --out directory. Each shard is resume-safe and only writes its own windows:
#   python3 lora/restyle_map.py --chunks /tmp/tinysz-nanshan --out /tmp/tinysz-nanshan-restyled \
#     --quality medium --shard 0/8 --concurrency 4
#   python3 lora/restyle_map.py --chunks /tmp/tinysz-nanshan --out /tmp/tinysz-nanshan-restyled --stitch-only
# `--count-only` reports non-fog windows (the ones that would be billed) with no API calls.
# `--windows x,y;x,y` restyles an explicit list. The pipeline CLI also accepts
# `--quality medium` on a single image: `npm run cli -- restyle --in p.png --look pixelsg --quality medium`.

# 3. land/water constraint (free): composite procedural pixels back wherever
#    the restyle invented water on land, or paved over real water. Open land
#    is tone-matched so the patch is not a flat beige sheet. Invented rooftop
#    gardens on built roofs are restored to the procedural roof.
node --max-old-space-size=4096 lora/render_water_mask.mjs \
  --out /tmp/tinysz-futian/water_mask.png
python3 lora/water_constrain.py \
  --chunks /tmp/tinysz-futian \
  --restyled /tmp/tinysz-futian-restyled \
  --mask /tmp/tinysz-futian/water_mask.png \
  --preview /tmp/tinyshenzen-futian-restyled-preview.jpg \
  --full /tmp/tinyshenzen-futian-restyled.png

# 4. slice both pyramids into the viewer (generated tiles are gitignored).
#    For the three-district core, slice tile-wise from each district's
#    constrained windows so a full-size canvas is never decoded:
#    python3 lora/slice_frontend.py --chunks /tmp/tinysz-core \
#      --out tinyworlds-frontend/public/data/tinysz-core --name tinysz-core \
#      --district /tmp/tinysz-nanshan:/tmp/tinysz-nanshan-restyled/tiles-constrained \
#      --district /tmp/tinysz-futian:/tmp/tinysz-futian-restyled/tiles-constrained \
#      --district /tmp/tinysz-luohu:/tmp/tinysz-luohu-restyled/tiles-constrained \
#      --tilewise
# Districts are reprojected from their own geo origin (Houhai, Caiwuwei)
# into the core world's Civic Center origin.
#
# After slicing, a free --polish pass feathers the dark renderer fog at
# the map edge into the viewer's beige (no stair-stepped border at low
# zoom) and fills blocky or missing bay water from the land/water mask
# with a tone-matched sample. --polish-vignette is the preferred free
# edge pass: it reslices the edge from the source restyle windows (not
# from already-faded tiles), keeps the bay-water fill and south-shore
# land restore, builds one coarse coverage mask with a wide blur and
# morphological close/open so the boundary does not follow source-tile
# edges, bicubically upsamples the distance field, and applies a single
# vignette (~1–2 tiles) to viewer beige whose falloff is displaced by
# low-frequency noise so the edge looks organic rather than stepped.
# Changing fade or smoothing parameters reuses the clean staging and
# only rebuilds the field and fade. It is resume-safe and idempotent
# from source. Do not run --polish immediately before it: the per-tile
# feather is what made the quilted fade. --polish-edge is the earlier
# global-fade attempt and is kept for the existing tests.
# python3 lora/slice_frontend.py \
#   --chunks /tmp/tinysz-core \
#   --out tinyworlds-frontend/public/data/tinysz-core --name tinysz-core \
#   --district /tmp/tinysz-nanshan:/tmp/tinysz-nanshan-restyled/tiles-constrained \
#   --district /tmp/tinysz-futian:/tmp/tinysz-futian-restyled/tiles-constrained \
#   --district /tmp/tinysz-luohu:/tmp/tinysz-luohu-restyled/tiles-constrained \
#   --polish-vignette
python3 lora/slice_frontend.py \
  --image /tmp/tinyshenzen-futian-restyled.png \
  --chunks /tmp/tinysz-futian \
  --out tinyworlds-frontend/public/data/tinysz \
  --restyle-dir tiles-restyled --procedural-dir tiles
cd tinyworlds-pipeline && TINY_OUT=../tinyworlds-frontend/public/data \
  npm run cli -- --city tinysz-core index
cd ../tinyworlds-frontend && NEXT_PUBLIC_WORLD=tinysz-core npm run dev
```

A single-window restyle of a preview still works through the pipeline CLI
(`npm run cli -- restyle --in preview.png --out out.png --look pixelsg`).

To train our own image-edit LoRA on those teacher tiles (Qwen-Image-Edit-2511,
a 24 GB GPU, MultiDiffusion over the same hem) see [`lora/README.md`](lora/README.md).
Teacher images and weights are not committed. That path needs a rented GPU
(roughly US$2–6 for train plus a Futian infer) and is not started from this
repository's default setup.

## Layout

| Path | What |
|------|------|
| `tinyworlds-pipeline/` | TypeScript CLI: downloads the data, builds the scene, renders the tiles and indexes |
| `tinyworlds-frontend/` | Next.js 16 static site: the map viewer |

The upstream tinyworlds API, video films, deploy scripts and GitHub Actions workflows are not
included here.

## Licence and attribution

Code is [MIT](LICENSE). The pipeline and viewer originated in
[nihey/tinyworlds](https://github.com/nihey/tinyworlds), © 2026 Nihey Takizawa; modifications
for Tiny Shenzhen are © 2026 Vincent Koc. The MIT notice is kept in full.

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) (ODbL) and the
[Overture Maps Foundation](https://overturemaps.org/). Supplementary footprints and per-building
heights come from [3D-GloBFP](https://doi.org/10.1038/s41597-024-03917-6) (Che, Y., Li, X.,
Liu, X., et al., 2024, CC BY 4.0). Remaining unmapped heights are taken from
[CNBH-10m](https://doi.org/10.5281/zenodo.7923866) (Wu, W., et al., 2023, CC BY 4.0).
The rendered tiles carry those attribution requirements.

The idea of a city redrawn tile by tile as isometric pixel art comes from
[tinyrj](https://tinyrj.com/) and isometric.nyc, as credited by tinyworlds. The
painted look of the optional restyle is inspired by maps such as Pixel Singapore;
this is an independent work, and no third-party map images are used or shipped.

## Credits

- [Nihey Takizawa](https://github.com/nihey) and [tinyworlds](https://github.com/nihey/tinyworlds)
- [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors
- [Overture Maps Foundation](https://overturemaps.org/)
- [East Asian Buildings](https://doi.org/10.5281/zenodo.8174931) (CC BY 4.0), via Overture
- [3D-GloBFP](https://doi.org/10.1038/s41597-024-03917-6) (Che et al., CC BY 4.0)
- [CNBH-10m](https://doi.org/10.5281/zenodo.7923866) (Wu et al., CC BY 4.0)
