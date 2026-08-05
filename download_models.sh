#!/usr/bin/env bash
# Downloads only the 4 MiniMax H3 files needed by the i2v workflow
# onto the mounted network volume at /workspace.
set -x

export HF_HOME=/workspace/.hf/home
export HF_XET_HIGH_PERFORMANCE=0

M=/workspace/ComfyUI_data/models
mkdir -p "$M/diffusion_models" "$M/text_encoders" "$M/vae" /workspace/.hf/home /workspace/dl_status

python3 -m pip -q install -U huggingface_hub

get() {
  local src="$1" dst="$2"
  if [ -f "$dst" ]; then echo "SKIP (exists): $dst"; return 0; fi
  hf download Comfy-Org/MiniMax-H3 "$src" \
    --repo-type model \
    --local-dir /workspace/.hf/stage \
    --max-workers 2 || return 1
  mkdir -p "$(dirname "$dst")"
  mv "/workspace/.hf/stage/$src" "$dst"
  echo "OK $dst"
}

get diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors \
    "$M/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors"

get vae/minimax_h3_video_vae_fp16.safetensors \
    "$M/vae/minimax_h3_video_vae_fp16.safetensors"

get vae/minimax_h3_audio_vae_fp32.safetensors \
    "$M/vae/minimax_h3_audio_vae_fp32.safetensors"

get text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors \
    "$M/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"

rm -rf /workspace/.hf/stage

echo "=== FINAL SIZES ==="
find "$M" -name "*.safetensors" -printf "%s\t%p\n" | sort -nr
du -sh "$M"

echo DOWNLOAD_COMPLETE > /workspace/dl_status/DONE
