# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

RunPod serverless image-to-video pipeline using ComfyUI with MiniMax H3
(`MiniMaxH3ImageToVideo` node, from the `10Eros`-adjacent template image
`brunorovoletto/minimax-h3-ltx-2.3-comfyui:cuda130`). The user submits an
image + prompt; a RunPod worker runs ComfyUI and uploads the resulting
video+audio to Cloudflare R2; the local client downloads it. Sibling project
to `Grey_chicken API` (the LTX-Video I2V pipeline) — same shape, different
model/workflow, separate repo and RunPod endpoint.

## Running locally

```bash
export RUNPOD_API_KEY=...
export RUNPOD_ENDPOINT_ID=...

# CLI
python run_job.py path/to/image.png -p "your prompt" -d 5 [-o output.mp4]

# Web UI (http://localhost:8000)
python serve.py
```

## Key architecture

### Local side
- `run_job.py` — CLI client. Loads `minimax_h3_i2v_API.json` (flat API-format
  dict keyed by node id), patches in the input image (node `114`), prompt/
  width/height/length (node `104`, `MiniMaxH3ImageToVideo`), and optionally a
  fixed seed (node `15`, `RandomNoise`). Sends to RunPod `/run`, polls
  `/status`/`/stream`, downloads the video via `extract_video_bytes()`.
- `serve.py` — Single-file stdlib web server wrapping
  `run_job.run_private_job()`. Each job runs on a background thread via a
  single-consumer queue (serial execution). Browser polls `/queue_list` every
  5s. Logs each submission to `prompts.csv`.

### Docker image (RunPod worker)
- Base: `brunorovoletto/minimax-h3-ltx-2.3-comfyui:cuda130` — a prebuilt image
  with MiniMax H3's models already baked in (no network volume, no custom
  node installs needed).
- `patch_handler.py` runs at build time and patches `/handler.py`, assumed to
  be a `runpod/worker-comfyui` fork: passes
  `bucket_name=os.environ.get("BUCKET_NAME")` to `rp_upload.upload_image` so
  videos go to R2 instead of the default month-year bucket. This patch is
  required — the build fails loudly if the expected marker isn't found. A
  second, optional patch (aliasing a `gifs` output key to `images`) is
  skipped rather than failing if unneeded — `SaveVideo` is a core ComfyUI
  node, unlikely to need it like `VHS_VideoCombine` does.

### Workflow

`minimax_h3_i2v_API.json` is hand-flattened from `video_minimax_h3_i2v_UI.json`
(the ComfyUI subgraph export, kept for reference — subgraphs aren't valid in
RunPod's API-format `/run` submission). Node map:

- `114` `LoadImage` — first_frame, patched with the uploaded image's filename
- `104` `MiniMaxH3ImageToVideo` — prompt / width / height / length, all
  patched by `run_job.py`. Width/height are computed from the input image's
  aspect ratio (768px short edge, capped at 1344px long edge, multiple of
  32 — H3's native canvas) rather than ComfyUI's `ResolutionSelector` node
  (dropped; it defaulted to a fixed "1:1 Square" regardless of input aspect).
  Length is computed from `--duration` seconds via
  `max(5, round(duration*24))` snapped up to the nearest `17k+5` frame count.
- `15` `RandomNoise` — noise_seed, patched only if `--seed` is passed
  (otherwise uses the workflow's baked-in default)
- `6`/`13`/`11`/`24` — model/CLIP/VAE loaders (video + audio VAE), static
- `91` `CreateVideo` / `92` `SaveVideo` — muxes decoded frames + audio into
  an mp4; unverified against a real ComfyUI export (see README "Known
  unknowns")

Nodes dropped from the UI export as dead/unnecessary: `115`
(`ResolutionSelector`, replaced by Python-computed dims), `107`/`111`
(`ComfyMathExpression`/`PrimitiveFloat`, replaced by Python-computed frame
length), `116`-`120` (markdown notes and a disconnected image-size
demo group).

### R2 / storage
Reuses the same Cloudflare R2 bucket/credentials as the `Grey_chicken API`
pipeline (see its `BUCKET_NAME` / `BUCKET_ENDPOINT_URL` / `BUCKET_ACCESS_KEY_ID`
/ `BUCKET_SECRET_ACCESS_KEY` env vars) — just a different RunPod endpoint.

## Deploying a new Docker image

Build `Dockerfile`, push to your registry, update the container image in the
RunPod endpoint settings. No CI is wired up yet (unlike `Grey_chicken API`'s
GitHub Actions build).
