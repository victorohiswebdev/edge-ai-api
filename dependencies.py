"""Reusable FastAPI dependencies."""

# Currently just re-exports get_db from database.
# Add auth, rate-limiting, query helpers here later.
from database import get_db  # noqa: F401
