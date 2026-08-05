"""Manage the MiniMax H3 RunPod pod: start, stop, status.

The pod runs ComfyUI from `brunorovoletto/minimax-h3-ltx-2.3-comfyui:cuda130`
with network volume `minimax_h3_models` mounted at /workspace, so the ~42GB of
model weights are already present — no download on boot.

Usage:
    python pod.py start [--gpu "NVIDIA GeForce RTX 5090"]
    python pod.py status
    python pod.py stop        # terminate; GPU billing ends
"""

import argparse
import json
import os
import sys
import time

import requests

GRAPHQL_URL = "https://api.runpod.io/graphql"

VOLUME_ID = "7enri8r9gz"          # minimax_h3_models, 150GB
DATACENTER = "EUR-IS-1"           # volume lives here; pod must match
IMAGE = "brunorovoletto/minimax-h3-ltx-2.3-comfyui:cuda130"
POD_NAME = "minimax-h3"

# EUR-IS-1 options. RTX 5090 (32GB) is verified sufficient: a 5s/768x960
# generation peaked at 26.3GB of 33.7GB. Bigger cards exist if you push
# resolution or duration: "NVIDIA A100-SXM4-80GB" ($1.59/hr),
# "NVIDIA RTX PRO 6000 Blackwell Server Edition" (96GB, $2.09/hr).
DEFAULT_GPU = "NVIDIA GeForce RTX 5090"

KEY_FILE = os.path.expanduser("~/.runpod_key")
SSH_PUBKEY_FILE = os.path.expanduser("~/.ssh/runpod_key.pub")


def api_key():
    key = os.environ.get("RUNPOD_API_KEY")
    if key:
        return key.strip()
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            return f.read().strip()
    sys.exit(f"No API key: set RUNPOD_API_KEY or write it to {KEY_FILE}")


def gql(query):
    r = requests.post(
        GRAPHQL_URL,
        params={"api_key": api_key()},
        json={"query": query},
        timeout=60,
    )
    r.raise_for_status()
    res = r.json()
    if res.get("errors"):
        sys.exit("RunPod API error: " + json.dumps(res["errors"], indent=2))
    return res["data"]


def find_pod():
    """Return the running MiniMax H3 pod dict, or None."""
    pods = gql("query { myself { pods { id name desiredStatus } } }")["myself"]["pods"]
    for p in pods:
        if p["name"] == POD_NAME:
            return p
    return None


def pod_details(pod_id):
    q = """query { pod(input: {podId: "%s"}) {
      id name desiredStatus
      runtime { uptimeInSeconds ports { ip privatePort publicPort type isIpPublic } }
    } }""" % pod_id
    return gql(q)["pod"]


def comfy_url(pod_id):
    """Public HTTPS proxy URL for ComfyUI's port 8188 on this pod."""
    return f"https://{pod_id}-8188.proxy.runpod.net"


def ssh_command(details):
    for p in ((details.get("runtime") or {}).get("ports") or []):
        if p["type"] == "tcp" and p["privatePort"] == 22:
            return f"ssh -i ~/.ssh/runpod_key root@{p['ip']} -p {p['publicPort']}"
    return None


def start(gpu):
    existing = find_pod()
    if existing:
        print(f"Pod already exists: {existing['id']} ({existing['desiredStatus']})")
        return existing["id"]

    pubkey = ""
    if os.path.exists(SSH_PUBKEY_FILE):
        with open(SSH_PUBKEY_FILE) as f:
            pubkey = f.read().strip()

    q = """mutation { podFindAndDeployOnDemand(input: {
      cloudType: SECURE
      gpuCount: 1
      gpuTypeId: "%s"
      dataCenterId: "%s"
      networkVolumeId: "%s"
      volumeMountPath: "/workspace"
      containerDiskInGb: 40
      minVcpuCount: 8
      minMemoryInGb: 24
      name: "%s"
      imageName: "%s"
      ports: "8188/http,22/tcp"
      allowedCudaVersions: ["13.0"]
      env: [{key: "PUBLIC_KEY", value: "%s"}]
    }) { id name desiredStatus } }""" % (
        gpu, DATACENTER, VOLUME_ID, POD_NAME, IMAGE, pubkey,
    )
    pod = gql(q)["podFindAndDeployOnDemand"]
    print(f"Started pod {pod['id']} on {gpu}")
    print(f"ComfyUI will be at: {comfy_url(pod['id'])}")
    print("\nBoot takes ~3-7 min (image pull + ComfyUI start). Poll with:")
    print("  python pod.py status")
    return pod["id"]


def stop():
    pod = find_pod()
    if not pod:
        print("No pod running.")
        return
    gql('mutation { podTerminate(input: {podId: "%s"}) }' % pod["id"])
    print(f"Terminated pod {pod['id']} — GPU billing stopped.")
    print(f"Volume {VOLUME_ID} persists (storage billing continues).")


def status():
    pod = find_pod()
    if not pod:
        print("No pod running.")
        return
    d = pod_details(pod["id"])
    rt = d.get("runtime") or {}
    print(f"Pod:     {d['id']}  ({d['desiredStatus']})")
    print(f"Uptime:  {rt.get('uptimeInSeconds')}s")
    url = comfy_url(d["id"])
    print(f"ComfyUI: {url}")
    ssh = ssh_command(d)
    if ssh:
        print(f"SSH:     {ssh}")

    # Is ComfyUI actually serving yet?
    try:
        r = requests.get(f"{url}/system_stats", timeout=10)
        if r.status_code == 200:
            print("\nComfyUI is UP and responding.")
            print(f"Set this and run generations:\n  export COMFYUI_POD_URL={url}")
            return
        print(f"\nComfyUI returned HTTP {r.status_code} — still booting.")
    except requests.RequestException as e:
        print(f"\nComfyUI not responding yet ({type(e).__name__}) — still booting.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start", help="Launch the pod")
    s.add_argument("--gpu", default=DEFAULT_GPU, help="GPU type id (default: %(default)s)")
    sub.add_parser("stop", help="Terminate the pod (stops GPU billing)")
    sub.add_parser("status", help="Show pod state and ComfyUI URL")
    args = ap.parse_args()

    if args.cmd == "start":
        start(args.gpu)
    elif args.cmd == "stop":
        stop()
    else:
        status()


if __name__ == "__main__":
    main()
