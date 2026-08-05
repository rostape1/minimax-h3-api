# MiniMax H3 API

Image-to-video generation using ComfyUI + MiniMax H3 on a RunPod GPU pod.
Submit an image + prompt, get back a video with native audio.

MiniMax H3 jointly generates video and stereo audio (voice, SFX, music) in a
single forward pass, up to ~15s at 24fps.

**Separate from the `Grey_chicken API` project** (LTX-Video pipeline) — own
repo, own RunPod volume, own scripts. Nothing shared.

## Setup (one time)

Already done, recorded here for reference:

- Network volume `minimax_h3_models` (`7enri8r9gz`), 150GB, **EUR-IS-1**,
  preloaded with ~42GB of model weights (see `NOTES.md`).
- RunPod API key in `~/.runpod_key` (needs `api.runpod.io/graphql` Read/Write).
- Passphrase-less SSH key at `~/.ssh/runpod_key` for pod shell access.

## Usage

```bash
# 1. Start the pod (~3-7 min to boot: image pull + ComfyUI start)
python pod.py start

# 2. Wait until ComfyUI answers
python pod.py status

# 3. Point the client at it (status prints this line)
export COMFYUI_POD_URL=https://<pod_id>-8188.proxy.runpod.net

# 4a. Generate from the CLI
python run_job.py image.png -p "your prompt" -d 5

# 4b. …or use the web UI at http://localhost:8000
python serve.py

# 5. Stop paying for the GPU when done
python pod.py stop
```

The pod stays warm between generations — the ~42GB model load happens once on
the first generation, not per job. Stop the pod when you're finished; the
volume (and its models) persists.

## Scripts

| File | Purpose |
|---|---|
| `pod.py` | Start / stop / check the RunPod GPU pod |
| `run_job.py` | CLI: one image + prompt → one video |
| `serve.py` | Local web UI (localhost:8000) with a serial job queue |
| `download_models.sh` | One-time model fetch onto the volume (already run) |
| `minimax_h3_i2v_API.json` | ComfyUI workflow, API format |
| `video_minimax_h3_i2v_UI.json` | Original ComfyUI subgraph export (reference) |

## Cost

- **Volume:** ~$10.50/month (150GB), always billing.
- **GPU:** only while a pod runs. EUR-IS-1 options:
  RTX 5090 32GB $0.99/hr · A100 80GB $1.59/hr ·
  RTX PRO 6000 96GB $1.89-2.09/hr.

## Open questions (untested)

- **VRAM needed:** estimated 30-40GB if all models stay resident, possibly
  24-32GB given ComfyUI's sequential offloading. The default GPU in `pod.py`
  is the 96GB card to be safe; try the 32GB RTX 5090 (`--gpu "NVIDIA GeForce
  RTX 5090"`) to cut cost roughly in half if it fits.
- **Workflow correctness:** `minimax_h3_i2v_API.json` was hand-flattened from
  the subgraph export and has never been run. If ComfyUI rejects it, open the
  UI JSON in the pod's ComfyUI (port 8188) and re-export via "Save (API
  Format)".
- **Startup time:** ~3-7 min is an estimate, not measured.
