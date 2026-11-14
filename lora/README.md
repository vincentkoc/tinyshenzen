# Pixel-Singapore-inspired LoRA for Tiny Shenzhen

A small **image-edit LoRA** trained on **our own** teacher tiles: procedural
`tinysz` renders paired with OpenAI `--look pixelsg` restyles of those same
renders. The LoRA’s job is to redraw a new procedural window in that look, so
a MultiDiffusion pass can restyle the whole Futian map without calling a
closed API at map scale.

Teacher images are **not** committed (they live under `/workspace/tinysz-teacher`
on the build box). This folder holds the manifest, the prep/train/infer
scripts, and these steps. Do **not** train on Pixel Singapore (Yadong Xie) or
any other third-party map tiles without permission. Do not send those images
to an API as a style reference either — the look is described in words.

## Why Qwen-Image-Edit-2511

| Option | Fits 24 GB? | Why we picked or passed |
| --- | --- | --- |
| **Qwen-Image-Edit-2511** + musubi-tuner LoRA | Yes, with `--fp8_base --fp8_scaled --blocks_to_swap 20` (documented ~24 GB; control images need a little more than the non-edit 16-block figure). 64 GB host RAM recommended. | **Chosen.** Same family as Pixel Singapore (Qwen-Image + a style LoRA). We have paired control/target images, and an *edit* model is built to keep the control’s geometry. |
| Qwen-Image (text-to-image only) | Same VRAM band | Weaker at holding our footprints — we would have to hope the prompt preserves layout. |
| DiffSynth-Studio fine-tune of Qwen-Image-Edit | ~48 GB for a full fine-tune | Too big for a 24 GB card. |
| FLUX.2 Klein | Yes, smaller | A reasonable fallback, also trained with control images in musubi-tuner, but less prior art on isometric pixel-city LoRAs and not the family Pixel Singapore used. |

Weights must be the Comfy-Org **bf16** checkpoints. musubi-tuner cannot use
`fp8_e4m3fn` as `--dit`.

- DiT: `qwen_image_edit_2511_bf16.safetensors` from [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI)
- VAE: `qwen_image_vae.safetensors` (same repo / Qwen-Image VAE)
- Text encoder: `qwen_2.5_vl_7b.safetensors` from [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI)

## Phase 1 — teacher set

From the repo root, with the pipeline built and `OPENAI_API_KEY` set (never
print it):

```bash
cd tinyworlds-pipeline && npx tsc -p tsconfig.json && cd ..

# 40 diverse 1024×1024 windows over the Futian test box
python3 lora/collect_teacher.py --out /workspace/tinysz-teacher

# After the first five, inspect /workspace/tinysz-teacher/restyled/ and the
# contact sheet. Re-run the same command to resume the rest (already-written
# files are skipped).
python3 lora/collect_teacher.py --out /workspace/tinysz-teacher --sheet-only
```

Pairs land in `procedural/` and `restyled/`. The contact sheet is
`/workspace/tinysz-teacher/sheet.jpg`. None of these images are committed.

The OpenAI full-map restyle (overlapping 1024 windows, 256 px hem, 465 calls
on the 23500×11175 Futian test render) is a separate, paid path:

```bash
node --max-old-space-size=4096 lora/render_full.mjs --out /workspace/tinysz-futian
python3 lora/restyle_map.py \
  --chunks /workspace/tinysz-futian \
  --out /workspace/tinysz-futian-restyled \
  --preview /workspace/tinyshenzen-futian-restyled-preview.jpg \
  --full /workspace/tinyshenzen-futian-restyled.png \
  --concurrency 4
```

`restyle_map.py` checkpoints every window and can be re-run safely.
`--quality medium` (or `low` / `high`) is passed through to the OpenAI Images API.
`--count-only` walks the windows, skips the API, and writes how many are fog
(those are not billed). To speed a large box, split the work across N workers
that share one `--out` directory; each `--shard I/N` (0-based) is a disjoint
slice, appends `usage.jsonl` under a file lock, and leaves the stitch to a
final `--stitch-only` pass:

```bash
node --max-old-space-size=4096 lora/render_full.mjs \
  --city tinysz-nanshan --out /workspace/tinysz-nanshan
python3 lora/restyle_map.py --chunks /workspace/tinysz-nanshan \
  --out /workspace/tinysz-nanshan-restyled --quality medium --shard 0/8
# …shards 1/8 through 7/8, same --out…
python3 lora/restyle_map.py --chunks /workspace/tinysz-nanshan \
  --out /workspace/tinysz-nanshan-restyled --stitch-only
```

`--windows x,y;x,y` restyles an explicit list of window origins instead.

The restyle can invent rivers on pale land (and sometimes pave over real
water). `render_water_mask.mjs` paints the scene's ground kinds the same way
the chunk renderer does, and `water_constrain.py` composites a tone-matched
fill — with a feathered edge — wherever the restyle shows water on land,
or the procedural pixels wherever it paved over real water. Open land is
harmonized to nearby restyle ground so the patch is not a flat beige sheet.
Invented rooftop gardens on built roofs are restored to the procedural roof.
Glass is left alone because it is already teal in the procedural image.
This is free: it does not call the API.

```bash
node --max-old-space-size=4096 lora/render_water_mask.mjs \
  --out /workspace/tinysz-futian/water_mask.png
python3 lora/water_constrain.py \
  --chunks /workspace/tinysz-futian \
  --restyled /workspace/tinysz-futian-restyled \
  --mask /workspace/tinysz-futian/water_mask.png \
  --preview /workspace/tinyshenzen-futian-restyled-preview.jpg \
  --full /workspace/tinyshenzen-futian-restyled.png
```

To put the constrained stitch into the viewer, slice it onto the same 512-px
grid `makeGrid` uses (`origin` −12288,−6144 for the Futian test box). Write
both pyramids so Settings can switch. Then re-run `index` with
`TINY_OUT` pointing at the viewer's data dir so the restyle field and place
indexes stay on the manifest.

```bash
python3 lora/slice_frontend.py \
  --image /workspace/tinyshenzen-futian-restyled.png \
  --chunks /workspace/tinysz-futian \
  --out tinyworlds-frontend/public/data/tinysz \
  --restyle-dir tiles-restyled --procedural-dir tiles
cd tinyworlds-pipeline && TINY_OUT=../tinyworlds-frontend/public/data \
  npm run cli -- --city tinysz index
```

Generated images are not committed (`public/data/` is gitignored).

## Phase 2 — dataset, train, MultiDiffusion

```bash
# 1. pairs → musubi-tuner edit layout (targets in images/, controls in controls/)
python3 lora/prepare_dataset.py \
  --teacher /workspace/tinysz-teacher \
  --out /workspace/tinysz-teacher/dataset

# 2. CPU tests for tiling, the hem, and dataset layout (no GPU, no API)
cd lora && python3 test_tiles.py

# 3. On a 24 GB GPU box: install musubi-tuner, download the bf16 weights into
#    lora/models/, then:
export MUSUBI_DIR=$HOME/musubi-tuner
export MODELS_DIR=$(pwd)/lora/models
bash lora/train.sh
# Default: rank 16, 16 epochs, 2 repeats, ~1,280 steps on 40 pairs.
# Raise BLOCKS_TO_SWAP if you OOM (edit controls cost extra VRAM).

# 4. Tiled inference over the Futian chunks, same 1024 / 256 hem as training
python3 lora/infer_multidiffusion.py \
  --chunks /workspace/tinysz-futian \
  --lora lora/output/tinysz-pixelsg-edit.safetensors \
  --backend musubi \
  --out /workspace/tinysz-futian-lora \
  --preview /workspace/tinyshenzen-futian-lora-preview.jpg \
  --full /workspace/tinyshenzen-futian-lora.png
```

The stitch uses a smooth-step hem identical to `edgeWeight` in
`tinyworlds-pipeline/src/restyle.ts`, written in horizontal bands so a 23 k-px
canvas does not need a multi-gigabyte float buffer.

`--backend identity` copies each source window and is what the stitch unit
test uses. It is also a way to debug the hem without a GPU.

## Phase 3 — GPU, not started from this box

This machine has no GPU, and no GPU-cloud key is present in the environment
(`RUNPOD_API_KEY`, `VAST_API_KEY`, `MODAL_TOKEN`, `REPLICATE_API_TOKEN`,
`FAL_KEY`, and similar names were checked; values were not read). **Do not
start a paid GPU job until a key is provided.**

Recommended when we are ready: **RunPod** community cloud, an RTX 4090 (24 GB)
or 5090, image with CUDA 12 + PyTorch. Env var: `RUNPOD_API_KEY`. Vast.ai is
often a little cheaper (`VAST_API_KEY`) but less predictable. Modal
(`MODAL_TOKEN`) and fal / Replicate trainers are convenient and usually more
expensive per hour for a multi-hour LoRA.

Rough time and cost, 24 GB card, musubi-tuner fp8 + block swap (slower than a
48 GB card):

- Weight download: ~40–60 GB, 10–20 min, no GPU needed for the fetch.
- Latent + text-encoder cache: 10–20 min.
- Training (~1,280 steps at ~8–15 s/it): **2–5 hours**. If we raise rank or
  epochs, budget a day.
- Futian MultiDiffusion (465 windows, 20 steps): **2–6 hours**, depending on
  block-swap speed. Checkpointed, so it can pause.
- **Total: about 5–12 hours.** At typical 2026 RunPod 4090 community prices
  (~US$0.34–0.44/h) that is **about US$2–6**; Vast 4090 spots are often
  ~US$0.20–0.34/h, **about US$1.50–4**. A 48 GB card costs more per hour and
  can drop `--blocks_to_swap`, so wall-clock (and sometimes dollars) goes
  down. This is tens of dollars, not thousands, and is the reason a LoRA is
  cheaper than restyling `tinysz-full` through a closed image API.

## Files

| Path | What it is |
| --- | --- |
| `teacher-manifest.json` | 40 lon/lat windows and tags. Committed. |
| `collect_teacher.py` | Preview + OpenAI restyle + contact sheet. |
| `prepare_dataset.py` | Pairs → musubi-tuner `images/` + `controls/` + `dataset.toml`. |
| `train.sh` | Cache latents, cache text encoder, train the LoRA. |
| `infer_multidiffusion.py` | Tiled LoRA inference and hem stitch. |
| `render_full.mjs` | Chunked procedural render (`--city` selects tinysz, tinysz-nanshan, tinysz-luohu, …). |
| `restyle_map.py` | OpenAI tiled restyle of that render (resume-safe, `--quality`, `--shard I/N`, `--windows`, `--count-only`). |
| `render_water_mask.mjs` | Rasterize real water/land from the scene's ground kinds. |
| `water_constrain.py` | Feathered composite that undoes invented (or missing) water. |
| `slice_frontend.py` | Cut the stitch into the viewer's 512-px webp pyramid. `--polish` feathers dark edge fog and fills bay water; `--polish-vignette` reslices the edge from source and applies one smooth global beige vignette with a wide, organically-wobbled coverage fade (idempotent, preferred); `--polish-water --polish-forest` (after the vignette) put the bay on one teal without tile steps, fade water on its own field, and cross-fade gray-washed near-edge land to the clean staging and even forest greens inland; `--polish-flats` fades featureless edge blotches into the fog; `--polish-fogline` (last) clears dashed traces and gray-washed windows at the fog boundary; `--polish-edge` is the earlier global-fade pass. No API calls. |
| `tiles.py`, `blend.py` | Shared window grid and hem. |
| `test_tiles.py` | CPU unit tests. |
