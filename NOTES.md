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

## Decision: network volume + custom Serverless handler (chosen 2026-08-05)

Rejected the "bake ~60-80GB of weights into the image" path — instead, reuse
the pattern the sibling Grey_chicken API project already uses: a RunPod
**network volume** mounted into the Serverless endpoint, with models living
on the volume instead of in the image. This keeps the image at ~25GB (base
only) and avoids needing a beefier-than-GitHub-Actions build environment.

Plan:

1. **Populate the volume once via a temporary Pod.**
   - Create a RunPod network volume (or reuse `dusty_blush_monkey` if size/
     region allows — check its free space first; MiniMax H3's weights are
     likely 40-80GB on top of whatever Grey_chicken API already stores
     there).
   - Launch a Pod from `brunorovoletto/minimax-h3-ltx-2.3-comfyui:cuda130`
     with that volume attached at `/workspace`.
   - Run `/workspace/install_minimax_h3.sh` with `INSTALL_TARGET=workspace`
     (non-interactive) to pull weights onto the volume.
   - Stop/terminate the pod once done (stop paying for it) — the volume
     persists independently.

2. **Point ComfyUI at the volume.** Same mechanism as Grey_chicken API's
   `extra_model_paths.yaml` pointing at `/runpod-volume` — figure out the
   exact base path MiniMax H3's installer used on the volume (from
   `post_start.sh`'s `COMFYUI_WORKSPACE_DATA_DIR=/workspace/ComfyUI_data`)
   and wire the Serverless container's `extra_model_paths.yaml` to match.

3. **Write a real `handler.py`** (replaces the `patch_handler.py`-patches-an-
   existing-handler approach, which doesn't apply here — no handler exists
   to patch). Needs to:
   - Start ComfyUI as a background subprocess on container boot
     (`main.py` at `/app/ComfyUI`, per the earlier `docker inspect`/`ls`
     findings), wait for it to report ready (poll its HTTP API).
   - On each job: accept the same `{workflow, images}` input shape the
     existing pipeline uses, write the uploaded image into ComfyUI's input
     dir, POST to `/prompt`, poll `/history/{id}`, extract the output video.
   - Upload the result to R2 (reuse the `BUCKET_NAME` env-var convention).
   - Install the `runpod` Python package in the `Dockerfile` (confirmed
     absent from the base image).

4. **Update `Dockerfile`** to: install `runpod` + any missing Python deps,
   copy in the new `handler.py`, set `CMD`/`ENTRYPOINT` to run it instead of
   `/post_start.sh`. Drop `patch_handler.py` entirely (nothing to patch).

5. **Create the Serverless endpoint** with the network volume attached,
   pointing at `ghcr.io/rostape1/minimax-h3-worker:latest` (or whatever tag
   the new build produces).

Known open question: cold-start time. Loading weights off a network volume
into VRAM is not instant — expect real cold-start latency (rough guess:
1-3 min), unlike a fully baked image. Not blocking, just a tradeoff to be
aware of vs. path 2's "bigger image, faster cold start."

## Loose ends / cleanup when resuming

- `.github/workflows/inspect-handler.yml` is a scratch inspection tool, kept
  in the repo for now — fine to keep, delete, or reuse for further probing
  (e.g. to find ComfyUI's exact startup command/port-ready signal for the
  new `handler.py`).
- `patch_handler.py` is now dead weight (nothing to patch) — delete once the
  real `handler.py` exists.
- `Dockerfile` needs rework per the plan above (install `runpod`, copy in
  `handler.py`, override `CMD`).
- GPU sizing note (from earlier discussion): the qwen3vl 32B text encoder
  alone is roughly 16GB even quantized; total VRAM needs are likely 25-35GB.
  RunPod's "5090" GPU option needs its actual VRAM figure double-checked in
  the console (32GB vs 48GB matters here) whichever path is chosen.
