"""Patch /handler.py in the brunorovoletto/minimax-h3-ltx-2.3-comfyui image so it
uploads outputs to a user-named S3/R2 bucket (via BUCKET_NAME env var) instead of
the default month-year bucket.

This image is presumed to be a worker-comfyui fork (same handler.py shape as
runpod/worker-comfyui). The gifs->images aliasing patch used in the LTX pipeline
is applied only if that marker exists here too — SaveVideo (a core ComfyUI node,
unlike VHS_VideoCombine) may already report under the 'images' key, so it's
optional rather than a hard requirement.
"""
import pathlib, sys

p = pathlib.Path("/handler.py")
src = p.read_text()

# --- Patch 1 (optional): gifs -> images, only if this image's handler needs it ---
marker1 = '            if "images" in node_output:'
if marker1 in src:
    inject1 = (
        '            # patched: treat VHS_VideoCombine gifs as images\n'
        '            if "gifs" in node_output:\n'
        '                node_output["images"] = node_output.get("images", []) + node_output.pop("gifs")\n'
    )
    src = src.replace(marker1, inject1 + marker1, 1)
    print("patch_handler.py: gifs aliased to images")
else:
    print("patch_handler.py: marker1 not found — skipping gifs->images alias (handler.py shape differs)")

# --- Patch 2 (required): pass bucket_name=os.environ["BUCKET_NAME"] to rp_upload.upload_image ---
marker2 = "rp_upload.upload_image(job_id, temp_file_path)"
if marker2 not in src:
    sys.exit("patch_handler.py: marker2 not found — handler.py source differs from expected worker-comfyui shape")

replacement2 = 'rp_upload.upload_image(job_id, temp_file_path, bucket_name=os.environ.get("BUCKET_NAME"))'
src = src.replace(marker2, replacement2, 1)

p.write_text(src)
print("handler.py patched: BUCKET_NAME env honored")
