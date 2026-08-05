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

MiniMax H3 expects a **structured** prompt, not loose prose. The official
guides are in `docs/`:

| Guide | Use for |
|---|---|
| `docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md` | T2VA / **I2VA** / FL2VA / L2VA — what this pipeline uses |
| `docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md` | Full-reference mode (the R2V workflow) |

This pipeline is **I2VA** (one image = first frame). That format is:

```text
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, <the subject
in Picture 1, preserving appearance/clothing/position>. The camera pushes in with
small amplitude at slow speed as <action>. The woman with a warm, husky voice (S1)
says: <d>[English] Your line here.</d> [Shot 2] At 00:06.500, the camera cuts to
<new information>.

overall_soundscape: <ambient + physical action sounds, 1-4 sentences. No dialogue or music here.>

non_diegetic_music: <score only the audience hears — instrumentation, tempo, dynamics. Or N/A.>
```

Rules that matter most:

- **First line is the instruction**, then a blank line, then the three fields.
- **`[Shot 1]` opens with the style** (`Live-action, cinematic`, `vintage film`,
  `3D CG`, …) and the initial composition — derive it from your input image.
- **Shot 1 gets no timestamp.** Later shots start with a strictly increasing
  cut time inside the duration: `[Shot 2] At 00:06.500, the camera cuts to…`
- **Camera motion = type + amplitude + speed**, written as natural prose:
  `pushes in with small amplitude at slow speed`. Types include Push In/Pull
  Out, Pan, Truck, Tilt, Pedestal, Arc Shot, Tracking Shot, Static Shot, POV.
- **Dialogue:** speaker ID `(S1)` outside the tag, only the language tag and
  the words inside: `(S1) says: <d>[English] What's for lunch?</d>`
  Establish voice qualities (pitch, timbre, rate) on first appearance.
- **On-screen text** goes in double quotes, verbatim.
- **Keep the three sections separate** — don't put dialogue or diegetic music
  in `overall_soundscape`.

**Duration note:** frame counts snap to a 17-frame grid, so `-d 10` yields 243
frames = **10.125s**. Keep cut times inside the real duration.

Ready-made examples live in `prompts/`.

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
