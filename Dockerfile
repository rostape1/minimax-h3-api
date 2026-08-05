FROM brunorovoletto/minimax-h3-ltx-2.3-comfyui:cuda130

# Patch handler.py so R2/S3 uploads honor BUCKET_NAME instead of the default
# month-year bucket. See patch_handler.py for details.
COPY patch_handler.py /tmp/patch_handler.py
RUN python3 /tmp/patch_handler.py && rm /tmp/patch_handler.py
