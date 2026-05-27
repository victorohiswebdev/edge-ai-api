"""
Edge AI Farm API — FastAPI backend for the Smart Farming Dashboard.

Serves sensor data from the SQLite database (written by the Pi's data_logger)
to the Next.js frontend via REST endpoints.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
from config import settings
from routes import sensors, ingest, system


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the database on startup, clean up on shutdown."""
    init_db()
    yield


app = FastAPI(
    title="Edge AI Farm API",
    description=(
        "Backend for the Integrated Edge AI Smart Farming Dashboard. "
        "Serves live and historical sensor data from the Arduino + Pi pipeline."
    ),
    version="1.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)

# ── CORS — Allow the Next.js dashboard to call the API ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ──
app.include_router(sensors.router, prefix="/api/v1", tags=["Sensors"])
app.include_router(ingest.router, prefix="/api/v1", tags=["Ingest"])
app.include_router(system.router, prefix="/api/v1", tags=["System"])


# ── Root — Health check ──
@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "Edge AI Farm API",
        "version": "1.0.0",
        "docs": "/docs",
    }
