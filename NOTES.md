# Deployment findings (2026-08-05) — paused, resume here

## Where things stand

- Repo created: https://github.com/rostape1/minimax-h3-api (public, same GitHub
  account as Grey_chicken API / ruling-gray-chicken-worker: `rostape1`)
- `run_job.py`, `serve.py`, `minimax_h3_i2v_API.json` (hand-flattened from the
  ComfyUI subgraph export) are written and committed — untested against a
  real endpoint.
- `Dockerfile` + `patch_handler.py` were written assuming this image is a
  `runpod/worker-comfyui` fork. **That assumption is wrong** (see below) —
  do not deploy them as-is.
- A `.github/workflows/inspect-handler.yml` (workflow_dispatch only) is in the
  repo for poking at the base image via GitHub Actions' disk, since pulling
  the ~25GB image locally was ruled out (limited local disk) and Docker Hub
  is also firewalled in this sandbox by default (needs allowlisting).

## Key finding: `brunorovoletto/minimax-h3-ltx-2.3-comfyui:cuda130` is a RunPod **Pod** image, not a Serverless worker

Inspected via GitHub Actions (`docker inspect`, `find`, `cat /start.sh`,
`cat /post_start.sh`):

- No `/handler.py`, no `runpod` Python package installed.
- `ENTRYPOINT ["/usr/bin/tini", "--"]`, `CMD ["/post_start.sh"]`.
- Exposes ports **22** (SSH) and **8188** (ComfyUI web UI) — the classic
  RunPod Pod template shape, not the Serverless worker shape.
- `/start.sh` (baked into the image, separate from the `CMD`) sets up nginx,
  SSH, Jupyter, then `sleep infinity` — pure Pod bootstrapping.
- `/post_start.sh` (the actual `CMD`, ~1100 lines) preps a persistent
  `/workspace` layout and **writes out** `install_ltx_23.sh` and
  `install_minimax_h3.sh` — installers that download models from Hugging
  Face **at pod boot**, with an interactive prompt ("container disk or
  volume disk?") defaulted to non-interactive-safe only via
  `INSTALL_TARGET=app`.

## Key finding: model weights are NOT baked into the 25GB image

The ~25GB image is just CUDA 13 + PyTorch + ComfyUI + custom nodes. MiniMax
H3's actual weights (32B-param text encoder even quantized, diffusion model,
two VAEs) are pulled from Hugging Face on first boot via `hf download` into
`/app/ComfyUI_data` or `/workspace/ComfyUI_data`. Realistic total install
size is likely 40-80GB beyond the base image.

## Why "just add patch_handler.py" doesn't work here

The whole `patch_handler.py` + `Dockerfile` approach (copied from
Grey_chicken API) assumed a `runpod/worker-comfyui`-shaped `/handler.py` to
patch. This image has no such file — it's designed to be rented as an
interactive Pod, not invoked as a Serverless request handler.

## Two real paths forward (unresolved — pick up here)

1. **Use RunPod Pods instead of Serverless.** Matches what this image is
   actually built for. Rent a persistent GPU pod, let its own installer pull
   models once, then talk to ComfyUI directly over HTTP on port 8188 — the
   Grey_chicken API repo already has this exact pattern (`run_pod_job()` in
   its `run_job.py`), just needs porting/adapting. Tradeoff: pay for the pod
   while it's running/idle, not true scale-to-zero.

2. **Build a real custom Serverless handler.** Requires baking model weights
   into the image at build time (running the installer non-interactively
   during `docker build`, `INSTALL_TARGET=app`) — produces a much larger
   image (~60-80GB), which free GitHub Actions runners are probably too
   disk-constrained to build (already ate most of the free disk just
   pulling the 25GB base). Would need a beefier build environment. Then a
   real `handler.py` needs to: start ComfyUI as a subprocess, wait for
   readiness, submit/poll jobs via its HTTP API, upload results to R2.
   Bigger lift, real infra cost, but true serverless economics.

Decision on which path (or a hybrid) was not made — pick this up with the
user before writing more code.

## Loose ends / cleanup when resuming

- `.github/workflows/inspect-handler.yml` is a scratch inspection tool, kept
  in the repo for now — fine to keep, delete, or reuse for further probing.
- If going with path 1 (Pods): `Dockerfile`/`patch_handler.py` in this repo
  are dead weight and should be removed; `run_job.py` needs a `run_pod_job()`
  equivalent instead of (or alongside) `run_private_job()`.
- If going with path 2 (custom Serverless): need a build environment with
  more disk/time than GitHub's free tier, and `patch_handler.py` needs to be
  replaced entirely with a real `handler.py` (not a patch to an existing
  one).
- GPU sizing note (from earlier discussion): the qwen3vl 32B text encoder
  alone is roughly 16GB even quantized; total VRAM needs are likely 25-35GB.
  RunPod's "5090" GPU option needs its actual VRAM figure double-checked in
  the console (32GB vs 48GB matters here) whichever path is chosen.
