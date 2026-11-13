#!/usr/bin/env bash
# Train a Qwen-Image-Edit-2511 LoRA on our teacher pairs, fitted to a 24 GB GPU.
#
# Why this model: Pixel Singapore / isometric.nyc / HypeSeoul all sit on the
# Qwen-Image family. We have (procedural, restyled) pairs, so an *image-edit*
# LoRA is the right shape: the procedural tile is the control, the teacher
# restyle is the target, geometry is held by the control image. musubi-tuner
# documents a 24 GB recipe (fp8 + --blocks_to_swap). DiffSynth fine-tunes want
# ~48 GB. FLUX.2 Klein would also fit, but it is a weaker match for this exact
# "edit our render" job and has less prior art on isometric pixel-city LoRAs.
#
# Do not start this on a box without a GPU. Do not train on third-party map
# tiles (Pixel Singapore included) without permission — only our teacher set.
#
# Env (all optional, with defaults):
#   MUSUBI_DIR   clone of https://github.com/kohya-ss/musubi-tuner
#   MODELS_DIR   bf16 Comfy-Org weights (DiT + VAE + Qwen2.5-VL)
#   DATASET_TOML from lora/prepare_dataset.py
#   OUTPUT_DIR   where the LoRA is written
#   BLOCKS_TO_SWAP  default 20 (edit control images need a little more than 16)

set -euo pipefail

MUSUBI="${MUSUBI_DIR:-$HOME/musubi-tuner}"
MODELS="${MODELS_DIR:-$(cd "$(dirname "$0")" && pwd)/models}"
DATASET="${DATASET_TOML:-/workspace/tinysz-teacher/dataset/dataset.toml}"
OUT="${OUTPUT_DIR:-$(cd "$(dirname "$0")" && pwd)/output}"
BLOCKS="${BLOCKS_TO_SWAP:-20}"
EPOCHS="${MAX_EPOCHS:-16}"
DIM="${NETWORK_DIM:-16}"
LR="${LEARNING_RATE:-5e-5}"

DIT="$MODELS/qwen_image_edit_2511_bf16.safetensors"
VAE="$MODELS/qwen_image_vae.safetensors"
TE="$MODELS/qwen_2.5_vl_7b.safetensors"

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "train.sh: no nvidia-smi. This script is meant for a 24 GB GPU box." >&2
  echo "See lora/README.md for the provider, env var, and cost estimate." >&2
  exit 1
fi
for f in "$DIT" "$VAE" "$TE" "$DATASET"; do
  if [ ! -e "$f" ]; then
    echo "train.sh: missing $f" >&2
    echo "Download Comfy-Org bf16 weights (not fp8_e4m3fn) — see lora/README.md." >&2
    exit 1
  fi
done
mkdir -p "$OUT"

echo "caching latents…"
python "$MUSUBI/src/musubi_tuner/qwen_image_cache_latents.py" \
  --dataset_config "$DATASET" \
  --vae "$VAE" \
  --model_version edit-2511

echo "caching text-encoder outputs…"
python "$MUSUBI/src/musubi_tuner/qwen_image_cache_text_encoder_outputs.py" \
  --dataset_config "$DATASET" \
  --text_encoder "$TE" \
  --batch_size 1 \
  --model_version edit-2511

echo "training LoRA (dim=$DIM, epochs=$EPOCHS, blocks_to_swap=$BLOCKS)…"
accelerate launch --num_cpu_threads_per_process 1 --mixed_precision bf16 \
  "$MUSUBI/src/musubi_tuner/qwen_image_train_network.py" \
  --dit "$DIT" \
  --vae "$VAE" \
  --text_encoder "$TE" \
  --model_version edit-2511 \
  --dataset_config "$DATASET" \
  --sdpa --mixed_precision bf16 \
  --timestep_sampling shift \
  --weighting_scheme none --discrete_flow_shift 2.2 \
  --optimizer_type adamw8bit --learning_rate "$LR" --gradient_checkpointing \
  --fp8_base --fp8_scaled --blocks_to_swap "$BLOCKS" \
  --max_data_loader_n_workers 2 --persistent_data_loader_workers \
  --network_module networks.lora_qwen_image \
  --network_dim "$DIM" --network_alpha "$DIM" \
  --max_train_epochs "$EPOCHS" --save_every_n_epochs 2 --seed 42 \
  --output_dir "$OUT" --output_name tinysz-pixelsg-edit \
  --logging_dir "$OUT/logs"

echo "done: $OUT/tinysz-pixelsg-edit.safetensors"
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader || true
