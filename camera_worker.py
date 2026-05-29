#!/usr/bin/env python3
"""Camera capture worker — takes a photo using Pi Camera Module via picamera2.

Called as a subprocess from the FastAPI endpoint to avoid picamera2
library conflicts with uvicorn's async workers.

Usage:
    python3 camera_worker.py [output_path]

Default output: ~/fyp/edge-ai-api/captures/snapshot_YYYYMMDD_HHMMSS.jpg
"""

import sys
import os
from datetime import datetime

CAPTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")
os.makedirs(CAPTURE_DIR, exist_ok=True)


def capture(filename=None):
    """Take a photo and save to file. Returns the file path."""
    if not filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(CAPTURE_DIR, f"snapshot_{ts}.jpg")

    try:
        from picamera2 import Picamera2
        import time

        picam2 = Picamera2()
        # Use preview configuration for quick capture
        config = picam2.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"}
        )
        picam2.configure(config)
        picam2.start()
        time.sleep(0.5)  # Let AEC/AGC settle
        picam2.capture_file(filename)
        picam2.close()
        return filename
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    result = capture(path)
    print(result)
