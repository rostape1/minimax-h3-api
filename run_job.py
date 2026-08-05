import argparse
import requests
import json
import time
import base64
import os
import struct

# --- YOUR CREDENTIALS ---
API_KEY = os.environ.get("RUNPOD_API_KEY", "")
ENDPOINT_ID = os.environ.get("RUNPOD_ENDPOINT_ID", "")

# --- DEFAULT PATHS ---
DEFAULT_WORKFLOW = "minimax_h3_i2v_API.json"
DEFAULT_IMAGE = "my_input_image.jpg"
DEFAULT_OUTPUT = "private_generated_video.mp4"

# --- CONFIG (API-format node ids, strings) ---
LOAD_IMAGE_NODE_ID = "114"
I2V_NODE_ID = "104"
SEED_NODE_ID = "15"
POLL_INTERVAL_SEC = 10
POLL_TIMEOUT_SEC = 60 * 90

# MiniMax H3's native canvas: 768px short edge, capped at 768x1344, multiple of 32.
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
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    f.read(3)
                    h, w = struct.unpack(">HH", f.read(4))
                    return w, h
                seg_len = struct.unpack(">H", f.read(2))[0]
                f.seek(seg_len - 2, 1)
    raise ValueError(f"Could not read image dimensions from {path}")


def compute_h3_dims(src_w, src_h):
    """Fit the input image's aspect ratio to H3's canvas: 768px short edge,
    capped at 1344px long edge, snapped to a multiple of 32."""
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


def extract_video_bytes(output):
    """Handle the common shapes a RunPod ComfyUI worker might return."""
    if output is None:
        raise ValueError("Worker returned no output")

    for key in ("message", "video", "video_base64", "data"):
        val = output.get(key) if isinstance(output, dict) else None
        if isinstance(val, str) and len(val) > 100:
            b64 = val.split(",", 1)[1] if "," in val[:64] else val
            return base64.b64decode(b64)

    for key in ("url", "video_url", "output_url"):
        val = output.get(key) if isinstance(output, dict) else None
        if isinstance(val, str) and val.startswith("http"):
            print(f"Downloading video from {val}")
            r = requests.get(val, timeout=300)
            r.raise_for_status()
            return r.content

    for key in ("images", "files", "videos"):
        items = output.get(key) if isinstance(output, dict) else None
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict):
                if first.get("url", "").startswith("http"):
                    r = requests.get(first["url"], timeout=300)
                    r.raise_for_status()
                    return r.content
                for k in ("data", "base64", "image"):
                    if isinstance(first.get(k), str):
                        s = first[k]
                        b64 = s.split(",", 1)[1] if "," in s[:64] else s
                        return base64.b64decode(b64)
            elif isinstance(first, str):
                b64 = first.split(",", 1)[1] if "," in first[:64] else first
                return base64.b64decode(b64)

    raise ValueError(f"Could not find video in worker output. Keys: {list(output.keys()) if isinstance(output, dict) else type(output)}")


def run_private_job(json_file_path, image_to_upload, output_video_name, prompt, duration=5.0, seed=None):
    with open(json_file_path, "r") as f:
        workflow = json.load(f)

    image_filename = os.path.basename(image_to_upload)
    with open(image_to_upload, "rb") as f:
        base64_image = base64.b64encode(f.read()).decode("utf-8")

    if LOAD_IMAGE_NODE_ID not in workflow:
        raise ValueError(f"LoadImage node id {LOAD_IMAGE_NODE_ID} not in workflow")
    workflow[LOAD_IMAGE_NODE_ID]["inputs"]["image"] = image_filename
    print(f"LoadImage node {LOAD_IMAGE_NODE_ID} -> {image_filename}")

    src_w, src_h = get_image_size(image_to_upload)
    width, height = compute_h3_dims(src_w, src_h)
    length = compute_length(duration)
    print(f"Input image: {src_w}x{src_h} -> canvas {width}x{height}, duration {duration}s -> {length} frames")

    if I2V_NODE_ID not in workflow:
        raise ValueError(f"MiniMaxH3ImageToVideo node id {I2V_NODE_ID} not in workflow")
    workflow[I2V_NODE_ID]["inputs"]["prompt"] = prompt
    workflow[I2V_NODE_ID]["inputs"]["width"] = width
    workflow[I2V_NODE_ID]["inputs"]["height"] = height
    workflow[I2V_NODE_ID]["inputs"]["length"] = length

    if seed is not None:
        workflow[SEED_NODE_ID]["inputs"]["noise_seed"] = seed
        print(f"Seed -> node {SEED_NODE_ID}: {seed}")

    payload = {
        "input": {
            "workflow": workflow,
            "images": [
                {"name": image_filename, "image": base64_image}
            ],
        }
    }

    url = f"https://api.runpod.ai/v2/{ENDPOINT_ID}/run"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    print("Sending job to minimax-h3-api...")
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    job_id = response.json().get("id")

    if not job_id:
        print("Error starting job:", response.text)
        return

    status_url = f"https://api.runpod.ai/v2/{ENDPOINT_ID}/status/{job_id}"
    stream_url = f"https://api.runpod.ai/v2/{ENDPOINT_ID}/stream/{job_id}"
    start = time.time()
    while True:
        if time.time() - start > POLL_TIMEOUT_SEC:
            print(f"\nTimeout after {POLL_TIMEOUT_SEC}s — job {job_id} still running. Check dashboard.")
            return

        try:
            stream_res = requests.get(stream_url, headers=headers, timeout=15).json()
            for msg in stream_res.get("stream") or []:
                output = msg.get("output") if isinstance(msg, dict) else msg
                print(f"  [worker] {output}")
        except Exception:
            pass

        res = requests.get(status_url, headers=headers, timeout=30).json()
        status = res.get("status")
        print(f"Status: {status}...")

        if status == "COMPLETED":
            video_bytes = extract_video_bytes(res.get("output"))
            with open(output_video_name, "wb") as f:
                f.write(video_bytes)
            print(f"\nSUCCESS! Video saved to: {os.path.abspath(output_video_name)}")
            return
        elif status in ("FAILED", "CANCELLED"):
            print("\nJob failed. Full response:")
            print(json.dumps(res, indent=2)[:2000])
            return

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send an image to RunPod for MiniMax H3 image-to-video generation.")
    parser.add_argument("image", nargs="?", default=DEFAULT_IMAGE, help="Path to input image (default: %(default)s)")
    parser.add_argument("-p", "--prompt", required=True, help="Prompt describing shots, motion, and audio")
    parser.add_argument("-d", "--duration", type=float, default=5.0, help="Target duration in seconds (default: %(default)s, max ~15)")
    parser.add_argument("-o", "--output", default=None, help="Output video filename (default: generated_video_<timestamp>.mp4)")
    parser.add_argument("-w", "--workflow", default=DEFAULT_WORKFLOW, help="Workflow JSON path (default: %(default)s)")
    parser.add_argument("-s", "--seed", type=int, default=None, help="Fixed noise seed (default: workflow's baked-in seed)")
    args = parser.parse_args()

    if not API_KEY or not ENDPOINT_ID:
        raise SystemExit("Set RUNPOD_API_KEY and RUNPOD_ENDPOINT_ID environment variables before running.")

    output_path = args.output or time.strftime("generated_video_%Y%m%d_%H%M%S.mp4")
    run_private_job(args.workflow, args.image, output_path, args.prompt, duration=args.duration, seed=args.seed)
