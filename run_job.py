"""Generate MiniMax H3 image-to-video clips on a running RunPod pod.

Talks directly to ComfyUI's HTTP API on the pod (port 8188), so the pod must
already be up:

    python pod.py start
    python pod.py status          # wait for "ComfyUI is UP"
    export COMFYUI_POD_URL=https://<pod_id>-8188.proxy.runpod.net
    python run_job.py image.png -p "your prompt" -d 5
"""

import argparse
import json
import os
import random
import string
import struct
import time
from urllib.parse import urlencode

import requests

POD_URL = os.environ.get("COMFYUI_POD_URL", "").rstrip("/")

DEFAULT_WORKFLOW = "minimax_h3_i2v_API.json"

# API-format node ids (see CLAUDE.md for the full node map)
LOAD_IMAGE_NODE_ID = "114"
I2V_NODE_ID = "104"
SEED_NODE_ID = "15"

POLL_INTERVAL_SEC = 5
POLL_TIMEOUT_SEC = 60 * 60

# MiniMax H3's native canvas: 768px short edge, capped at 1344, multiple of 32.
H3_SHORT_EDGE = 768
H3_LONG_EDGE_CAP = 1344
FPS = 24
FRAME_BLOCK = 17  # length must land on a (17k + 5) grid at 24fps


def get_image_size(path):
    """Return (width, height) for a PNG or JPEG file using stdlib only."""
    with open(path, "rb") as f:
        head = f.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", head[16:24])
            return w, h
        if head[:2] == b"\xff\xd8":
            f.seek(2)
            while True:
                byte = f.read(1)
                while byte and byte != b"\xff":
                    byte = f.read(1)
                while byte == b"\xff":
                    byte = f.read(1)
                marker = byte[0] if byte else 0
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9,
                              0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    f.read(3)
                    h, w = struct.unpack(">HH", f.read(4))
                    return w, h
                seg_len = struct.unpack(">H", f.read(2))[0]
                f.seek(seg_len - 2, 1)
    raise ValueError(f"Could not read image dimensions from {path}")


def compute_h3_dims(src_w, src_h):
    """Fit the image's aspect ratio to H3's canvas, snapped to a multiple of 32."""
    def snap(n):
        return max(32, int(round(n / 32)) * 32)

    aspect = src_w / src_h
    if aspect >= 1:
        height, width = H3_SHORT_EDGE, H3_SHORT_EDGE * aspect
    else:
        width, height = H3_SHORT_EDGE, H3_SHORT_EDGE / aspect
    width, height = snap(width), snap(height)

    if width > H3_LONG_EDGE_CAP:
        height = snap(height * (H3_LONG_EDGE_CAP / width))
        width = H3_LONG_EDGE_CAP
    if height > H3_LONG_EDGE_CAP:
        width = snap(width * (H3_LONG_EDGE_CAP / height))
        height = H3_LONG_EDGE_CAP
    return width, height


def compute_length(duration_sec):
    """Convert seconds to a valid frame length on H3's 17-frame-per-block grid."""
    frames = max(5, round(duration_sec * FPS))
    return frames + (5 - (frames % FRAME_BLOCK)) % FRAME_BLOCK


def build_workflow(json_file_path, image_filename, image_path, prompt, duration, seed=None):
    with open(json_file_path) as f:
        workflow = json.load(f)

    if LOAD_IMAGE_NODE_ID not in workflow:
        raise ValueError(f"LoadImage node id {LOAD_IMAGE_NODE_ID} not in workflow")
    workflow[LOAD_IMAGE_NODE_ID]["inputs"]["image"] = image_filename

    src_w, src_h = get_image_size(image_path)
    width, height = compute_h3_dims(src_w, src_h)
    length = compute_length(duration)
    print(f"Input {src_w}x{src_h} -> canvas {width}x{height}, {duration}s -> {length} frames")

    if I2V_NODE_ID not in workflow:
        raise ValueError(f"MiniMaxH3ImageToVideo node id {I2V_NODE_ID} not in workflow")
    node = workflow[I2V_NODE_ID]["inputs"]
    node["prompt"] = prompt
    node["width"] = width
    node["height"] = height
    node["length"] = length

    if seed is not None:
        workflow[SEED_NODE_ID]["inputs"]["noise_seed"] = seed
        print(f"Seed: {seed}")

    return workflow


def run_pod_job(json_file_path, image_to_upload, output_video_name,
                prompt=None, duration=5.0, seed=None, pod_url=None):
    """Upload the image to ComfyUI, queue the workflow, poll, download the video."""
    base = (pod_url or POD_URL).rstrip("/")
    if not base:
        raise ValueError("COMFYUI_POD_URL is not set — start a pod first (python pod.py start)")

    image_filename = os.path.basename(image_to_upload)
    workflow = build_workflow(
        json_file_path, image_filename, image_to_upload, prompt, duration, seed
    )

    print(f"Uploading {image_filename} to {base} ...")
    with open(image_to_upload, "rb") as f:
        r = requests.post(
            f"{base}/upload/image",
            files={"image": (image_filename, f, "image/octet-stream")},
            data={"overwrite": "true"},
            timeout=120,
        )
    r.raise_for_status()

    client_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    r = requests.post(
        f"{base}/prompt",
        json={"prompt": workflow, "client_id": client_id},
        timeout=60,
    )
    if r.status_code >= 400:
        print("ComfyUI rejected the workflow:")
        print(r.text[:3000])
        r.raise_for_status()
    prompt_id = r.json()["prompt_id"]
    print(f"Queued: {prompt_id}")

    start = time.time()
    while True:
        elapsed = int(time.time() - start)
        if elapsed > POLL_TIMEOUT_SEC:
            print(f"\nTimeout after {POLL_TIMEOUT_SEC}s — job {prompt_id} still running.")
            return

        try:
            hist = requests.get(f"{base}/history/{prompt_id}", timeout=30).json()
            if prompt_id in hist:
                entry = hist[prompt_id]
                st = entry.get("status", {})
                if st.get("completed"):
                    for node_out in entry.get("outputs", {}).values():
                        for key in ("gifs", "videos", "images"):
                            files = node_out.get(key, [])
                            if not files:
                                continue
                            fi = files[0]
                            params = urlencode({
                                "filename": fi["filename"],
                                "type": fi.get("type", "output"),
                                **({"subfolder": fi["subfolder"]} if fi.get("subfolder") else {}),
                            })
                            url = f"{base}/view?{params}"
                            print(f"Downloading {url}")
                            vr = requests.get(url, timeout=600)
                            vr.raise_for_status()
                            with open(output_video_name, "wb") as f:
                                f.write(vr.content)
                            print(f"\nSUCCESS! Saved to {os.path.abspath(output_video_name)}")
                            return
                    print("Completed, but no video found in outputs:")
                    print(json.dumps(entry.get("outputs", {}), indent=2)[:2000])
                    return
                if st.get("status_str") == "error":
                    print("Job failed:")
                    print(json.dumps(st, indent=2)[:3000])
                    return
        except requests.RequestException as e:
            print(f"  [poll error] {e}")

        print(f"  running... {elapsed}s")
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MiniMax H3 image-to-video on a RunPod pod.")
    parser.add_argument("image", help="Path to input image")
    parser.add_argument("-p", "--prompt", required=True,
                        help="Prompt describing shots, motion, and audio")
    parser.add_argument("-d", "--duration", type=float, default=5.0,
                        help="Target duration in seconds (default: %(default)s, max ~15)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output filename (default: generated_video_<timestamp>.mp4)")
    parser.add_argument("-w", "--workflow", default=DEFAULT_WORKFLOW,
                        help="Workflow JSON path (default: %(default)s)")
    parser.add_argument("-s", "--seed", type=int, default=None,
                        help="Fixed noise seed (default: workflow's baked-in seed)")
    parser.add_argument("--pod-url", default=None,
                        help="ComfyUI base URL (default: $COMFYUI_POD_URL)")
    args = parser.parse_args()

    output_path = args.output or time.strftime("generated_video_%Y%m%d_%H%M%S.mp4")
    run_pod_job(args.workflow, args.image, output_path, prompt=args.prompt,
                duration=args.duration, seed=args.seed, pod_url=args.pod_url)
