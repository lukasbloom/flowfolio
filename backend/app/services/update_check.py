"""GitHub Releases fetch + DB cache for the daily version check.

The daily cron calls run_version_check, which fetches the repo's latest
published release from the GitHub Releases API and caches it in user_setting
rows via update_store. GET /api/update-status then reads that cache only, no
GitHub call ever reaches the user's browser.

Threat notes:
  - The release html_url is parsed from an untrusted GitHub body and
    becomes a clickable link target. We validate it is an https://github.com/...
    URL before storing it; anything else is dropped (notes_url=None).
  - The fetch URL is built from settings.github_repo (operator config),
    pinned to api.github.com/repos/... never derived from request input.

Service-commits boundary: the pure fetch helper is commit-free; run_version_check
stages cache writes but the caller (the cron handler) commits.
"""
from __future__ import annotations

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import settings
from app.services.key_store import get_api_key
from app.services.update_store import set_cached_release, set_check_status

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
_GITHUB_URL_PREFIX = "https://github.com/"


def _validated_notes_url(html_url: object) -> str | None:
    """Return html_url only if it is an https://github.com/... URL."""
    if isinstance(html_url, str) and html_url.startswith(_GITHUB_URL_PREFIX):
        return html_url
    return None


async def fetch_latest_release(client: httpx.AsyncClient) -> dict[str, str | None]:
    """Fetch the latest published release for settings.github_repo.

    Returns {"version", "notes_url", "published_at"}; notes_url is None when the
    release html_url is not a trusted github.com URL. Raises on network / HTTP
    errors — run_version_check catches these into a recorded failure.
    """
    url = f"{GITHUB_API_BASE}/repos/{settings.github_repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json"}
    # Optional token raises the unauthenticated 60/hr limit. Resolved
    # from the DB-backed key store; only sent when configured; the daily cron is
    # fine on the unauthenticated limit.
    token = get_api_key("github")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = await client.get(
        url, headers=headers, timeout=settings.pricing_timeout_seconds
    )
    resp.raise_for_status()
    payload = resp.json()

    # tag_name is treated as an opaque version string (never shell-interpolated;
    # the image pull is compose-pinned).
    version = payload.get("tag_name")
    notes_url = _validated_notes_url(payload.get("html_url"))
    published_at = payload.get("published_at")
    return {
        "version": version,
        "notes_url": notes_url,
        "published_at": published_at,
    }


async def run_version_check(session: AsyncSession) -> dict[str, str | None]:
    """Fetch the latest release and cache it; record ok/failed status.

    Commit-free — the cron handler owns the transaction. A failed fetch is a
    SOFT failure: it records update_check_last_status=failed (so the cron does
    not hammer GitHub all day) and does NOT re-raise, leaving any previously
    cached release intact.
    """
    checked_at = clock.now().isoformat()
    try:
        async with httpx.AsyncClient() as client:
            release = await fetch_latest_release(client)
        await set_cached_release(
            session,
            version=release["version"],
            notes_url=release["notes_url"],
            published_at=release["published_at"],
        )
        await set_check_status(session, status="ok", checked_at=checked_at)
        logger.info(
            "version_check_ok",
            extra={"latest": release["version"]},
        )
        return {"status": "ok", "latest": release["version"]}
    except Exception as exc:
        await set_check_status(session, status="failed", checked_at=checked_at)
        logger.warning(
            "version_check_failed",
            extra={"err": f"{type(exc).__name__}: {exc}"},
        )
        return {"status": "failed", "latest": None}
