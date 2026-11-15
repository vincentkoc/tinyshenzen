# Image-edit LoRA for Tiny Shenzhen

A small **image-edit LoRA** trained on **our own** teacher tiles: procedural
`tinysz` renders paired with OpenAI `--look pixelsg` restyles of those same
renders. The LoRA’s job is to redraw a new procedural window in that look, so
a MultiDiffusion pass can restyle a whole district without calling an image
API window by window.

Teacher images and weights are **not** committed. This folder holds the
teacher manifest and the prep, train and inference scripts. It also holds the
full-map restyle scripts; see [docs/pipeline.md](../docs/pipeline.md).

Do **not** train on Pixel Singapore (Yadong Xie) or any other third-party map
tiles without permission. Do not send those images to an API as a style
reference either; the look is described in words.

## Why Qwen-Image-Edit-2511

| Option | Fits 24 GB? | Why we picked or passed |
| --- | --- | --- |
| **Qwen-Image-Edit-2511** + musubi-tuner LoRA | Yes, with `--fp8_base --fp8_scaled --blocks_to_swap 20` (documented ~24 GB; control images need a little more than the non-edit 16-block figure). 64 GB host RAM recommended. | **Chosen.** Same family as Pixel Singapore (Qwen-Image + a style LoRA). We have paired control/target images, and an *edit* model is built to keep the control’s geometry. |
| Qwen-Image (text-to-image only) | Same VRAM band | Weaker at holding our footprints; the prompt alone has to preserve the layout. |
| DiffSynth-Studio fine-tune of Qwen-Image-Edit | ~48 GB for a full fine-tune | Too big for a 24 GB card. |
| FLUX.2 Klein | Yes, smaller | A reasonable fallback, also trained with control images in musubi-tuner, but less prior art on isometric pixel-city LoRAs and not the family Pixel Singapore used. |

Weights must be the Comfy-Org **bf16** checkpoints. musubi-tuner cannot use
`fp8_e4m3fn` as `--dit`.

- DiT: `qwen_image_edit_2511_bf16.safetensors` from [Comfy-Org/Qwen-Image-Edit_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI)
- VAE: `qwen_image_vae.safetensors` (same repo / Qwen-Image VAE)
- Text encoder: `qwen_2.5_vl_7b.safetensors` from [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI)

## Phase 1: teacher set

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

The full-map restyle that made the live map (render, restyle, water
constraint, slicing and polish) is a separate path documented in
[docs/pipeline.md](../docs/pipeline.md) and
[docs/polish-passes.md](../docs/polish-passes.md).

## Phase 2: dataset, training, MultiDiffusion

```bash
# 1. pairs → musubi-tuner edit layout (targets in images/, controls in controls/)
python3 lora/prepare_dataset.py \
  --teacher /workspace/tinysz-teacher \
  --out /workspace/tinysz-teacher/dataset

# 2. CPU tests for tiling, the hem, and dataset layout (no GPU, no API)
(cd lora && python3 -m unittest discover -s . -p "test_*.py")

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

## Phase 3: GPU

Training and inference need a 24 GB GPU. Nothing in this repository starts
a GPU job on its own.

A rented RTX 4090 (24 GB) or 5090 with CUDA 12 and PyTorch is enough. RunPod
and Vast.ai both work; managed trainers such as Modal, fal or Replicate are
more convenient but cost more per hour for a multi-hour LoRA.

Rough timings on a 24 GB card with musubi-tuner fp8 and block swap:

| Step | Time |
| --- | --- |
| Weight download (~40–60 GB, no GPU needed) | 10–20 min |
| Latent and text-encoder cache | 10–20 min |
| Training (~1,280 steps at ~8–15 s/it) | 2–5 hours |
| Futian MultiDiffusion (465 windows, 20 steps, checkpointed) | 2–6 hours |

A 48 GB card can drop `--blocks_to_swap` and finishes sooner.

## Files

| Path | What it is |
| --- | --- |
| `teacher-manifest.json` | 40 lon/lat windows and tags. Committed. |
| `collect_teacher.py` | Preview + OpenAI restyle + contact sheet. |
| `prepare_dataset.py` | Pairs → musubi-tuner `images/` + `controls/` + `dataset.toml`. |
| `train.sh` | Cache latents, cache text encoder, train the LoRA. |
| `infer_multidiffusion.py` | Tiled LoRA inference and hem stitch. |
| `render_full.mjs` | Chunked procedural render of a city (`--city`). |
| `restyle_map.py` | Tiled image-model restyle of that render. Resume-safe, shardable. |
| `render_water_mask.mjs` | Real water and land mask from the scene's ground kinds (`--city`). |
| `water_constrain.py` | Feathered composite that undoes invented or missing water. |
| `slice_frontend.py` | Slices restyled windows into the viewer's 512-px webp pyramid and runs the polish passes ([docs/polish-passes.md](../docs/polish-passes.md)). No API calls. |
| `spots/` | One-off polish fixes per world, for `--polish-spots`. |
| `tiles.py`, `blend.py` | Shared window grid and hem. |
| `test_tiles.py` | CPU unit tests: `cd lora && python3 -m unittest discover -s . -p "test_*.py"`. |
