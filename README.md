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
- **GPU:** only while a pod runs. Default is the RTX 5090 at **$0.99/hr**
  (verified sufficient). Bigger cards in EUR-IS-1 if needed:
  A100 80GB $1.59/hr · RTX PRO 6000 96GB $2.09/hr.

## Measured performance (2026-08-05, RTX 5090, 5s @ 768x960)

| Metric | Result |
|---|---|
| Pod boot → ComfyUI ready | ~5 min |
| Peak VRAM | 26.3 / 33.7 GB |
| Sampling (20 steps) | 2:29 (7.5 s/it) |
| Total first generation | 4:47 (includes ~42GB model load) |
| Output | 124 frames @ 24fps, H.264 + AAC audio, ~870KB |

Later generations in the same session skip the model load, so expect roughly
2.5-3 min each.

## Notes

- **Host driver:** the image needs CUDA ≥13.0. `pod.py` passes
  `allowedCudaVersions: ["13.0"]` so RunPod only schedules onto hosts with a
  new enough driver — without it you get a container that crash-loops with
  `nvidia-container-cli: requirement error: unsatisfied condition: cuda>=13.0`.
- **ComfyUI's own GUI** is available at the same URL for interactive prompt
  iteration. The image also bundles official workflows (`MiniMax-H3_I2V.json`,
  `MiniMax-H3_T2V.json`, `MiniMax-H3_R2V.json`) loadable from its workflow
  menu.
- **ComfyUI logs** live at `/workspace/comfyui.log` on the pod (not container
  stdout) — `ssh ... "tail -f /workspace/comfyui.log"` to watch sampler
  progress.

