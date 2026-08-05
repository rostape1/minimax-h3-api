#!/usr/bin/env bash
#
# Download an extra MiniMax H3 model onto the pod's network volume.
#
# Usage (pod must be running):
#     ./add_model.sh ref2va          # reference-to-video (~21GB)
#     ./add_model.sh fl2va           # first/last-frame-to-video (already installed)
#     ./add_model.sh <repo/path.safetensors>   # any file in Comfy-Org/MiniMax-H3
#
# Reads the pod's SSH details from `pod.py status`, kicks the download off in
# the background on the pod, then tails progress until it finishes.

set -euo pipefail
cd "$(dirname "$0")"

REPO=Comfy-Org/MiniMax-H3
SSH_KEY=~/.ssh/runpod_key

# Friendly names -> path within the HF repo
case "${1:-}" in
  ref2va)
    SRC=diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors ;;
  fl2va)
    SRC=diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors ;;
  "")
    echo "usage: $0 {ref2va|fl2va|<path/in/repo.safetensors>}" >&2
    echo "  ref2va  reference-to-video model (~21GB), needed by the R2V workflow" >&2
    exit 1 ;;
  *)
    SRC="$1" ;;
esac

DEST_DIR=/workspace/ComfyUI_data/models/$(dirname "$SRC")
FNAME=$(basename "$SRC")
TAG=$(echo "$FNAME" | cut -d. -f1)

# --- locate the pod -------------------------------------------------------
SSH_LINE=$(python3 pod.py status 2>/dev/null | grep '^SSH:' | sed 's/^SSH: *//') || true
if [ -z "${SSH_LINE:-}" ]; then
  echo "No running pod (or SSH not ready). Start one first:" >&2
  echo "    python3 pod.py start" >&2
  exit 1
fi

# pod.py prints:  SSH:  ssh -i ~/.ssh/runpod_key root@<ip> -p <port>
HOST=$(echo "$SSH_LINE" | grep -oE '[a-z]+@[0-9.]+' | head -1)
PORT=$(echo "$SSH_LINE" | grep -oE '\-p +[0-9]+' | grep -oE '[0-9]+' | head -1)
if [ -z "$HOST" ] || [ -z "$PORT" ]; then
  echo "Could not parse SSH details from: $SSH_LINE" >&2
  exit 1
fi
SSH="ssh -i $SSH_KEY -o StrictHostKeyChecking=no -o LogLevel=ERROR $HOST -p $PORT"

echo "Pod:  $HOST:$PORT"
echo "File: $SRC"

# --- already there? -------------------------------------------------------
if $SSH "test -f $DEST_DIR/$FNAME"; then
  echo "Already installed: $DEST_DIR/$FNAME"
  $SSH "ls -la $DEST_DIR/$FNAME"
  exit 0
fi

# --- kick off the download in the background on the pod -------------------
STAGE=/workspace/.hf/stage_$TAG
LOG=/workspace/dl_$TAG.log

echo "Starting download in background on the pod..."
$SSH "nohup bash -c '
  export HF_HOME=/workspace/.hf/home HF_XET_HIGH_PERFORMANCE=0
  mkdir -p $DEST_DIR
  hf download $REPO $SRC --repo-type model --local-dir $STAGE --max-workers 2 &&
  mv $STAGE/$SRC $DEST_DIR/ &&
  rm -rf $STAGE &&
  echo MODEL_DOWNLOAD_DONE
' > $LOG 2>&1 & echo ok" > /dev/null

# --- follow along ---------------------------------------------------------
echo "Downloading (Ctrl-C to stop watching; the download continues on the pod)"
while true; do
  if $SSH "grep -q MODEL_DOWNLOAD_DONE $LOG 2>/dev/null"; then
    echo
    echo "Done:"
    $SSH "ls -la $DEST_DIR/$FNAME; echo; df -h /workspace | tail -1"
    echo
    echo "Refresh the ComfyUI browser tab to see the new model in the dropdown."
    exit 0
  fi
  if $SSH "grep -qiE 'error|traceback|No such file' $LOG 2>/dev/null"; then
    echo
    echo "Download failed:" >&2
    $SSH "tail -20 $LOG" >&2
    exit 1
  fi
  SZ=$($SSH "du -sh $STAGE 2>/dev/null | cut -f1" || echo "?")
  printf "\r  staged: %-10s" "${SZ:-0}"
  sleep 15
done
