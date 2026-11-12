# Tiny Shenzhen

An isometric pixel-art map of Shenzhen, China: the Civic Center, Ping An Finance Centre and
the Futian CBD, glass curtain-wall towers, white and pastel mid-rises, dense urban villages,
palms and the parks, generated from open data.

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

The style (`SHENZHEN` in `tinyworlds-pipeline/src/scene/styles.ts`) leans on glass curtain-wall
supertalls (in the manner of Ping An Finance Centre), white and pastel mid-rises, concrete and
brick urban villages (chengzhongcun / handshake buildings), and tropical flora. Named landmarks
— Ping An Finance Centre, KK100, Window of the World, Shenzhen Bay Park, China Resources
Headquarters, Shun Hing Square, the Civic Center and the Stock Exchange — are curated so they
can be searched, and the towers get explicit materials, tapers and heights.

Data comes from [OpenStreetMap](https://www.openstreetmap.org/copyright) and the
[Overture Maps Foundation](https://overturemaps.org/). Heights are taken from that data where
they are mapped, or estimated from the number of floors.

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
NEXT_PUBLIC_WORLD=tinysz npm run dev      # http://localhost:3000
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

Generated tiles and raw extracts are not committed (`tinyworlds-pipeline/data/` and
`tinyworlds-frontend/public/data/`). They are reproducible with the commands above.

The original tinyworlds city ids (New York, Las Vegas, and the rest) are still in the pipeline
if you want them; this repository is for Shenzhen.


## Optional: restyle with an image model

The map itself is still drawn by the procedural renderer. If you want a richer
isometric-pixel pass in the style of the maps people share on X (tiny.london,
Pixel Singapore, isometric.nyc), there is an optional `restyle` command. It cuts
a rendered PNG into overlapping windows, asks an image model to redraw each one
while keeping the geometry, and feathers the seams (a short "hem", so
neighbouring windows meet cleanly).

It is opt-in and reads a key from the environment. It never looks for credentials
on disk.

```bash
# recommended: Gemini 2.5 Flash Image — cheap, and in the same family as the
# "Nano Banana" / Gemini image models used as teachers on those isometric maps
export GEMINI_API_KEY=...          # from https://aistudio.google.com/apikey

# optional overrides
# export RESTYLE_MODEL=gemini-3-pro-image-preview   # higher quality, more expensive
# export RESTYLE_PROVIDER=openai
# export OPENAI_API_KEY=...
# export RESTYLE_MODEL=gpt-image-1

npm run cli -- restyle --in preview-civic-center.png --out civic-restyled.png
# defaults: 1024 px windows, 256 px overlap
```

Cost, roughly: a 1536×1024 preview is one or two windows (a few cents on Flash
Image). The Futian test canvas (~23,500×11,175 px, 1024 px windows stepped by
768) is about 450 calls — around US$15–25 with Flash Image, or US$40–70 with a
Pro or gpt-image model. The full urban-core render would be several times that.

Training a LoRA on teacher frames (the path used by tiny.london and Pixel
Singapore) is not included: it needs a GPU and a training set.

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
[Overture Maps Foundation](https://overturemaps.org/). The rendered tiles carry those
attribution requirements.

The idea of a city redrawn tile by tile as isometric pixel art comes from
[tinyrj](https://tinyrj.com/) and isometric.nyc, as credited by tinyworlds; this is an
independent, procedural take on it.

## Credits

- [Nihey Takizawa](https://github.com/nihey) and [tinyworlds](https://github.com/nihey/tinyworlds)
- [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors
- [Overture Maps Foundation](https://overturemaps.org/)
