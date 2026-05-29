"""Pydantic schemas for request/response validation."""

from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime


# ─── Response Models (API → Dashboard) ───


class SensorReading(BaseModel):
    """A single row from sensor_logs."""
    id: int
    timestamp: datetime
    moisture_zone_1: Optional[int] = None
    moisture_zone_2: Optional[int] = None
    moisture_zone_3: Optional[int] = None
    temperature_c: Optional[float] = None
    humidity_perc: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class LatestReading(BaseModel):
    """Most recent sensor snapshot for the live dashboard."""
    moisture_zone_1: Optional[int]
    moisture_zone_2: Optional[int]
    moisture_zone_3: Optional[int]
    temperature_c: Optional[float]
    humidity_perc: Optional[float]
    timestamp: datetime


class SensorSummary(BaseModel):
    """Averaged stats over a time window."""
    avg_moisture_1: Optional[float]
    avg_moisture_2: Optional[float]
    avg_moisture_3: Optional[float]
    avg_temperature: Optional[float]
    avg_humidity: Optional[float]
    reading_count: int
    from_timestamp: datetime
    to_timestamp: datetime


# ─── Request Models (Dashboard → API / Pi → API) ───


class SensorDataIngest(BaseModel):
    """Payload for posting a new sensor reading."""
    moisture_zone_1: int
    moisture_zone_2: int
    moisture_zone_3: int
    temperature_c: Optional[float] = None
    humidity_perc: Optional[float] = None
    timestamp: Optional[datetime] = None


class LiveReading(BaseModel):
    """Most recent real-time sensor reading (updated every 2s)."""
    moisture_zone_1: Optional[int]
    moisture_zone_2: Optional[int]
    moisture_zone_3: Optional[int]
    temperature_c: Optional[float]
    humidity_perc: Optional[float]
    timestamp: datetime
