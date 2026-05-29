"""Camera endpoints — capture snapshots and list recent captures."""

import os
import subprocess
import glob
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()

# ── Paths ──
WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "camera_worker.py")
CAPTURE_DIR = os.path.join(os.path.dirname(WORKER_SCRIPT), "captures")


class CaptureResponse(BaseModel):
    filename: str
    path: str
    timestamp: str
    size_bytes: int


class CaptureListResponse(BaseModel):
    captures: list[CaptureResponse]


@router.get("/camera/snapshot")
def take_snapshot():
    """Capture a photo using the Pi Camera Module and return it.

    Calls camera_worker.py as a subprocess, serves the resulting image.
    """
    if not os.path.exists(WORKER_SCRIPT):
        raise HTTPException(503, "Camera worker script not found")

    try:
        result = subprocess.run(
            ["python3", WORKER_SCRIPT],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            raise HTTPException(500, f"Camera capture failed: {result.stderr.strip()}")

        path = result.stdout.strip()
        if not path or not os.path.exists(path):
            raise HTTPException(500, "Camera produced no output file")

        stat = os.stat(path)
        return FileResponse(
            path,
            media_type="image/jpeg",
            filename=os.path.basename(path),
            headers={
                "X-Capture-Filename": os.path.basename(path),
                "X-Capture-Size": str(stat.st_size),
                "X-Capture-Timestamp": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            },
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Camera capture timed out (15s)")
    except FileNotFoundError:
        raise HTTPException(503, "python3 or picamera2 not available — is this a Raspberry Pi?")


@router.get("/camera/captures")
def list_captures(limit: int = 10):
    """List recent captures, newest first."""
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    files = sorted(
        glob.glob(os.path.join(CAPTURE_DIR, "snapshot_*.jpg")),
        key=os.path.getmtime, reverse=True,
    )[:limit]

    captures = []
    for f in files:
        stat = os.stat(f)
        captures.append(CaptureResponse(
            filename=os.path.basename(f),
            path=f,
            timestamp=datetime.fromtimestamp(stat.st_mtime).isoformat(),
            size_bytes=stat.st_size,
        ))

    return CaptureListResponse(captures=captures)


@router.get("/camera/captures/{filename}")
def get_capture(filename: str):
    """Serve a specific captured image by filename."""
    safe_path = os.path.normpath(os.path.join(CAPTURE_DIR, filename))
    if not safe_path.startswith(CAPTURE_DIR):
        raise HTTPException(400, "Invalid filename")
    if not os.path.exists(safe_path):
        raise HTTPException(404, "Capture not found")

    return FileResponse(safe_path, media_type="image/jpeg")
