# MiniMax H3 API

RunPod serverless image-to-video pipeline using ComfyUI with MiniMax H3
(`MiniMaxH3ImageToVideo`). Submit an image + prompt; a RunPod worker runs
ComfyUI and uploads the resulting video (with native audio) to Cloudflare R2;
the local client downloads it.

MiniMax H3 jointly generates video and stereo audio (voice, SFX, music) in a
single forward pass, up to ~15s at up to 2K/24fps. This pipeline runs its
**image-to-video** task specifically (first-frame in, video+audio out).

## Setup

```bash
export RUNPOD_API_KEY=...
export RUNPOD_ENDPOINT_ID=...
```

## Running locally

```bash
# CLI
python run_job.py path/to/image.png -p "your prompt" -d 5 [-o output.mp4]

# Web UI (http://localhost:8000)
python serve.py
```

## Architecture

- `run_job.py` — CLI client. Loads `minimax_h3_i2v_API.json`, patches in the
  input image, prompt, computed width/height (fit to H3's 768px-short-edge
  canvas from the image's aspect ratio), and frame length (from `--duration`
  seconds, snapped to H3's 17-frame-per-block grid at 24fps). Sends to RunPod
  `/run`, polls `/status`, downloads the video.
- `serve.py` — stdlib web server wrapping `run_job.run_private_job()`. Same
  serial job-queue pattern as the sibling LTX pipeline (Grey_chicken API).
- `Dockerfile` / `patch_handler.py` — builds on
  `brunorovoletto/minimax-h3-ltx-2.3-comfyui:cuda130` (models baked in) and
  patches its `/handler.py` so R2 uploads honor `BUCKET_NAME`.

## RunPod endpoint env vars required

Reuses the same Cloudflare R2 bucket as the Grey_chicken API pipeline:

| Var | Purpose |
|-----|---------|
| `BUCKET_NAME` | R2 bucket name |
| `BUCKET_ENDPOINT_URL` | `https://<account_id>.r2.cloudflarestorage.com` |
| `BUCKET_ACCESS_KEY_ID` | R2 API token key ID |
| `BUCKET_SECRET_ACCESS_KEY` | R2 API token secret |

## Known unknowns (verify on first real deploy)

- **`patch_handler.py` markers**: written defensively against the assumption
  that this image forks `runpod/worker-comfyui`'s `handler.py`. The
  `bucket_name` patch will hard-fail the build with a clear message if that's
  wrong; the optional `gifs`→`images` alias just gets skipped.
- **`minimax_h3_i2v_API.json`**: hand-flattened from the ComfyUI subgraph
  export (`video_minimax_h3_i2v_UI.json`, kept alongside for reference) since
  subgraphs aren't valid in RunPod's API-format submission. Node wiring was
  cross-checked against the UI JSON's `links` array, but two widget values
  couldn't be fully confirmed statically: `CreateVideo` (node `91`) only sets
  `fps`; a second widget value in the source (`8`) has no confirmed meaning
  and was dropped. If a real job fails at node `91` or `92`, open this
  workflow in an actual ComfyUI instance and use "Save (API Format)" to get
  a verified export, then diff against this file.
- **Not yet created**: the RunPod serverless endpoint itself. Deploy the
  image built from this repo's `Dockerfile`, then set `RUNPOD_ENDPOINT_ID`.

## Deploying a new Docker image

Build and push `Dockerfile` to your registry, then update the container image
in the RunPod endpoint settings.
