"""Shared FastAPI dependencies for the demo write-lock.

Single home for `forbid_in_demo` so the keys and self-update routers share one
canonical guard definition and never drift apart.
"""
from __future__ import annotations

from fastapi import HTTPException

from app.core.config import settings


def forbid_in_demo() -> None:
    """Hard-block writes/mutations in demo mode."""
    if settings.demo_mode:
        raise HTTPException(status_code=403, detail="Disabled in demo mode")
