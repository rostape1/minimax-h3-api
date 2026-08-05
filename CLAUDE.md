# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Image-to-video generation using ComfyUI + MiniMax H3 on a **RunPod Pod**
(not serverless). The user starts a GPU pod, ComfyUI boots with models
already present on an attached network volume, and the local client talks to
ComfyUI's HTTP API directly on port 8188.

**This is a separate project from `Grey_chicken API`** (the LTX-Video
serverless pipeline). They share no code, no endpoint, and no volume. Some
patterns were copied over (the queue web UI, ComfyUI history polling), but
the projects are independent — don't cross-wire them.

## Architecture

Three scripts, each with one responsibility:

- `pod.py` — RunPod GraphQL client. `start` / `stop` / `status` for the GPU
  pod. Hardcodes the volume id, datacenter, and image; reads the API key from
  `$RUNPOD_API_KEY` or `~/.runpod_key`.
- `run_job.py` — Generation client. Patches the workflow JSON, uploads the
  image to ComfyUI (`POST /upload/image`), queues it (`POST /prompt`), polls
  `GET /history/{id}`, downloads via `GET /view`. Requires
  `$COMFYUI_POD_URL`.
- `serve.py` — stdlib web server wrapping `run_job.run_pod_job()`. Serial
  job queue on a single background thread; browser polls `/queue_list`.
  Logs submissions to `prompts.csv`.

### Why Pods, not serverless

The base image (`brunorovoletto/minimax-h3-ltx-2.3-comfyui:cuda130`) is a
RunPod **Pod** template — no `/handler.py`, no `runpod` package, `CMD` is
`/post_start.sh`, exposes ports 22 and 8188. Making it serverless would mean
writing a ComfyUI-wrapping handler from scratch. Since the expected usage is
interactive batches (start pod → generate several → stop), a warm pod is both
simpler and cheaper: the ~42GB model load is paid once per session instead of
on every serverless cold start. See `NOTES.md` for the full investigation.

### Infrastructure (already provisioned)

- Network volume `minimax_h3_models`, id `7enri8r9gz`, 150GB, **EUR-IS-1**.
  Mounted at `/workspace`; models at `/workspace/ComfyUI_data/models/`.
  The pod must launch in EUR-IS-1 — volumes can't cross datacenters.
- The image's `post_start.sh` writes an `extra_model_paths.yaml` that already
  covers `/workspace/ComfyUI_data` (as `comfyui_workspace`), so ComfyUI finds
  the volume's models with no extra config.

## Workflow node map

`minimax_h3_i2v_API.json`, hand-flattened from `video_minimax_h3_i2v_UI.json`
(ComfyUI subgraph exports aren't valid API-format input):

- `114` `LoadImage` — first frame; patched with the uploaded filename
- `104` `MiniMaxH3ImageToVideo` — prompt / width / height / length, all
  patched by `run_job.py`. Width/height are computed in Python from the input
  image's aspect ratio (768px short edge, 1344 cap, multiple of 32 — H3's
  native canvas), replacing the UI's `ResolutionSelector` which defaulted to
  a fixed 1:1. Length is computed from `--duration` seconds and snapped up to
  the nearest `17k+5` frame count.
- `15` `RandomNoise` — noise_seed; patched only when `--seed` is passed
- `6`/`13`/`11`/`24` — UNet / CLIP / video VAE / audio VAE loaders, static
- `91` `CreateVideo` / `92` `SaveVideo` — mux frames + audio to mp4

Dropped from the UI export: `115` (`ResolutionSelector`), `107`/`111`
(math nodes for frame length — both now computed in Python), `116`-`120`
(markdown notes and a disconnected demo group).

**Unverified:** this JSON has never been run. Node `91`'s widget values in
particular are a guess (the UI export had a second value, `8`, with no
confirmed meaning — dropped). If ComfyUI rejects the workflow, load the UI
JSON in the running pod's ComfyUI and re-export with "Save (API Format)".

## Gotchas

- **Never override the image's `CMD`/`dockerArgs`** — `/post_start.sh` is what
  starts sshd and keeps the container alive. Overriding it yields a pod with
  no SSH and nothing running.
- **Pod needs ≥24GB system RAM.** 16GB OOM-crash-loops (exit 137) during
  heavy I/O.
- **SSH is blocked from the Claude Code sandbox** (outbound non-HTTP ports).
  Pod shell commands must be run from the user's own terminal via `!ssh ...`.
- **`urllib` gets 403 through the sandbox proxy** — use `requests` in scripts
  here.
- RunPod API hostnames need sandbox allowlisting: `api.runpod.io`,
  `rest.runpod.io`, `api.runpod.ai`.
