"""Pump control endpoints — command queue + status + emergency stop."""

from fastapi import APIRouter, Depends, HTTPException
from sqlite3 import Connection
from datetime import datetime
from database import get_db
from schemas import PumpCommandRequest, PumpStatus, EmergencyStopResponse

router = APIRouter()


@router.post("/pumps/command")
def enqueue_pump_command(req: PumpCommandRequest, db: Connection = Depends(get_db)):
    """Enqueue a pump ON/OFF command via the queue pattern.

    Checks:
      - zone is 1-3
      - command is ON or OFF
      - dedup: skip if zone is already in requested state
    """
    if req.zone not in (1, 2, 3):
        raise HTTPException(400, "Zone must be 1, 2, or 3")
    if req.command not in ("ON", "OFF"):
        raise HTTPException(400, "Command must be 'ON' or 'OFF'")

    # Dedup: check current state
    cur = db.execute("SELECT * FROM pump_status ORDER BY id DESC LIMIT 1").fetchone()
    col = f"pump_{req.zone}"
    if cur and cur[col] == req.command:
        return {
            "status": "duplicate",
            "message": f"Zone {req.zone} is already {req.command}",
        }

    # Enqueue
    db.execute(
        "INSERT INTO pump_commands (zone, command) VALUES (?, ?)",
        (req.zone, req.command),
    )
    db.commit()

    return {
        "status": "queued",
        "zone": req.zone,
        "command": req.command,
        "message": f"Pump {req.zone} {req.command} command queued",
    }


@router.get("/pumps/status")
def get_pump_status(db: Connection = Depends(get_db)):
    """Return the current pump state from pump_status table."""
    row = db.execute(
        "SELECT * FROM pump_status ORDER BY id DESC LIMIT 1"
    ).fetchone()

    if not row:
        return {
            "pump_1": "OFF",
            "pump_2": "OFF",
            "pump_3": "OFF",
            "updated_at": None,
        }

    return {
        "pump_1": row["pump_1"],
        "pump_2": row["pump_2"],
        "pump_3": row["pump_3"],
        "updated_at": row["updated_at"],
    }


@router.post("/pumps/emergency-stop")
def emergency_stop(db: Connection = Depends(get_db)):
    """Emergency All-Off — clears pending queue and writes all-off directly.

    Bypasses the queue for immediate safety response.
    The data_logger will pick up the INSERT in pump_status and
    also send the all-off command over serial.
    """
    # 1. Clear any pending commands
    db.execute("DELETE FROM pump_commands WHERE status = 'pending'")

    # 2. Insert a priority all-off command for each zone
    for z in (1, 2, 3):
        db.execute(
            "INSERT INTO pump_commands (zone, command, status) VALUES (?, ?, 'sent')",
            (z, "OFF"),
        )

    # 3. Set pump_status to all OFF
    db.execute("DELETE FROM pump_status")
    db.execute(
        "INSERT INTO pump_status (pump_1, pump_2, pump_3) VALUES ('OFF', 'OFF', 'OFF')"
    )
    db.commit()

    return EmergencyStopResponse(
        success=True,
        message="All pumps set to OFF. Pending commands cleared.",
    )
