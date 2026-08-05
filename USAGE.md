# How to use this

Quick reference for running MiniMax H3 image-to-video generation.
Everything happens from `/Users/peterrostas/minimax-h3-api`.

---

## TL;DR

```bash
cd /Users/peterrostas/minimax-h3-api

python3 pod.py start        # 1. rent the GPU  (~5 min to be ready)
python3 pod.py status       # 2. repeat until "ComfyUI is UP", copy the export line
export COMFYUI_POD_URL=https://<pod_id>-8188.proxy.runpod.net
python3 serve.py            # 3. open http://localhost:8000, generate

# 4. done? click "Stop pod" in the UI  (or: python3 pod.py stop)
```

**The GPU bills while the pod runs (~$0.99/hr). Stop it when you finish.**

---

## Starting up

### 1. Start the pod

```bash
cd /Users/peterrostas/minimax-h3-api
python3 pod.py start
```

Rents an RTX 5090 in Iceland (EUR-IS-1) and attaches the network volume that
holds the models. Takes **~5 minutes**: pulling the 25GB image is most of it,
then ComfyUI boots.

Need more VRAM (bigger resolution / longer clips)?

```bash
python3 pod.py start --gpu "NVIDIA A100-SXM4-80GB"                        # 80GB, $1.59/hr
python3 pod.py start --gpu "NVIDIA RTX PRO 6000 Blackwell Server Edition" # 96GB, $2.09/hr
```

### 2. Wait for it

```bash
python3 pod.py status
```

Re-run until you see **"ComfyUI is UP and responding."** Until then it says
"still booting" — that's normal, not an error.

### 3. Point the client at it

`status` prints the exact line to copy:

```bash
export COMFYUI_POD_URL=https://<pod_id>-8188.proxy.runpod.net
```

The pod ID is different every time, so copy it fresh each session. This is
per-terminal — open a new tab and you need to export it again.

### 4. Generate

**Web UI** (easiest):
```bash
python3 serve.py
```
→ http://localhost:8000

**Or CLI:**
```bash
python3 run_job.py myimage.png -p "your prompt" -d 5
```

---

## Using the web UI

The status bar at the top shows pod state, uptime, and running cost:

```
● RTX 5090    up 26m 17s    ~$0.43 @ $0.99/hr           [Stop pod]
```

**To generate:** enter a prompt, choose an image, set duration (1-15s), hit
**Generate**. Jobs run one at a time.

**To queue several:** click **+** under "Queue" to stage more while one runs.
Staged items survive a page refresh (kept in browser storage).

**When a job finishes:**
- **Play** — watch it in a new tab
- **Frame** — download the last frame (useful for chaining a follow-on clip)
- **Use prompt** — copy that prompt back into the main box

**Pod log** (bottom, click *Show*) — live sampler progress, e.g.
`65%|██████▌ | 13/20 [01:08<00:37, 5.30s/it]`, plus model loads and errors.

**Where videos go:** `generated/<job_id>.mp4`. The CLI writes to
`generated_video_<timestamp>.mp4` in the current directory unless you pass `-o`.

---

## Shutting down

**From the UI:** click **Stop pod**. It warns if a job is still running.

**From the terminal:**
```bash
python3 pod.py stop
```

Either way GPU billing ends immediately. The models stay on the volume, so
next start is just the boot time — no re-download.

Stopping `serve.py` (Ctrl-C) does **not** stop the pod. Only the button or
`pod.py stop` does.

---

## Writing prompts

MiniMax H3 generates **video and audio together**, so describe both. One block
covering look, shots, camera motion, and sound:

```
Sunlit beach portrait, natural daylight, warm summer tones.

SHOT 1: Opens exactly on the input image; she turns her head slowly toward
camera and smiles, hair moving in the sea breeze.

SHOT 2: Cut to a wider angle, palm fronds swaying behind her.

Audio: gentle ocean waves, distant beach chatter, soft wind.
```

Points that matter:
- **Always describe the audio** — it's the model's main differentiator.
- **Shot 1 should match your input image** (it becomes the first frame).
- **Timeline markers work too:** `[0s-1s]`, `[1s-2.5s]` instead of shot labels.
- **Put negatives inline:** "hard cuts only, no dissolves", "do not misspell text".
- There is **no separate negative-prompt field** (unlike the LTX pipeline).

One image is enough — it becomes the first frame and the model generates all
motion from there.

---

## Adding more models

The volume only holds the models needed for **I2V** (first/last-frame to
video). ComfyUI ships other MiniMax H3 workflows that need different weights —
open one and you'll see a red-highlighted "Load Diffusion Model" node if its
model is missing.

| Workflow tab | Needs | Installed? |
|---|---|---|
| `MiniMax-H3_I2V` | `minimax_h3_fl2va_pruned_int8_convrot` | yes |
| `MiniMax-H3_T2V` | same `fl2va` model (no image inputs connected) | yes |
| `MiniMax-H3_R2V` | `minimax_h3_ref2va_pruned_int8_convrot` | no — see below |

With the pod running:

```bash
./add_model.sh ref2va      # ~21GB, reference-to-video
```

It downloads on the pod straight onto the volume (so it's there for every
future session), shows progress, and skips if already installed. Ctrl-C only
stops the watching — the download keeps going on the pod.

Any other file from the `Comfy-Org/MiniMax-H3` repo works too:

```bash
./add_model.sh diffusion_models/minimax_h3_fl2va_bf16.safetensors
```

After it lands, **refresh the ComfyUI tab** so the model dropdown re-reads the
directory.

**fl2va vs ref2va:** `fl2va` animates *from* your image as the first frame
(what this project's pipeline uses). `ref2va` takes reference images/video/
audio and generates new footage matching them, rather than starting from the
frame.

---

## What it costs

| | Rate | When |
|---|---|---|
| GPU (RTX 5090) | $0.99/hr | only while the pod runs |
| Volume (150GB) | ~$10.50/mo | always, keeps models ready |

Volume is billed on **provisioned** size, not the ~42GB used. Shrinking would
mean making a new volume and re-downloading; saving ~$6/mo probably isn't
worth it, and the headroom allows adding more models later.

**Speed:** first generation of a session ~4.5 min (loads ~42GB into VRAM),
subsequent ones **~2 min**. So batch your work in one session rather than
starting and stopping repeatedly.

---

## When something breaks

**"No pod running — generations will fail."**
Start one: `python3 pod.py start`.

**Jobs vanished from the queue after restarting `serve.py`**
Job state is in memory, so restarting loses it. Work already sent to the pod
keeps running — recover the videos with:
```bash
curl -s "$COMFYUI_POD_URL/history?max_items=10" | python3 -m json.tool | grep filename
curl -s "$COMFYUI_POD_URL/view?filename=MiniMax_H3_00003_.mp4&type=output&subfolder=video" -o recovered.mp4
```

**Container crash-loops with `cuda>=13.0` driver errors**
The host's NVIDIA driver is too old. `pod.py` already filters for CUDA 13.0
hosts; if it happens anyway, stop and start again to land on a different
machine.

**Out of memory during sampling**
Shorter duration, or a bigger GPU (see options under "Start the pod").

**Need a shell on the pod**
`python3 pod.py status` prints the ssh command. Live ComfyUI log:
```bash
ssh -i ~/.ssh/runpod_key root@<ip> -p <port> "tail -f /workspace/comfyui.log"
```

**ComfyUI's own GUI** is at the same URL as `COMFYUI_POD_URL` — good for
interactive prompt tinkering. The image also bundles official workflows
(`MiniMax-H3_I2V.json`, `_T2V`, `_R2V`) in its workflow menu.

---

## Fixed infrastructure

Set up once; nothing to do unless it breaks.

| Thing | Value |
|---|---|
| Network volume | `minimax_h3_models` / `7enri8r9gz` / 150GB / EUR-IS-1 |
| Models on volume | `/workspace/ComfyUI_data/models/` (~42GB) |
| Image | `brunorovoletto/minimax-h3-ltx-2.3-comfyui:cuda130` |
| RunPod API key | `~/.runpod_key` (needs graphql Read/Write) |
| SSH key | `~/.ssh/runpod_key` (no passphrase) |

The pod **must** run in EUR-IS-1 — volumes can't cross datacenters.
