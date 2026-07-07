from pydantic import BaseModel


class VersionResponse(BaseModel):
    """The running version baked at build time. Plain string, no Decimal."""

    version: str


class UpdateStatusResponse(BaseModel):
    """Cached current-vs-latest + dismissal state for the banner.

    Served from the DB cache only — GET /api/update-status never calls GitHub.
    All fields are plain strings (no Decimal/money concern).
    """

    current_version: str
    latest_version: str | None
    update_available: bool
    release_notes_url: str | None
    dismissed: bool
    last_checked: str | None
    check_failed: bool

    # True on a source-mounted development build (app_version == "dev"). Self-update
    # is not possible there (no image to pull) and "dev" isn't comparable to a
    # release, so update_available is forced False and the UI shows a note instead
    # of an actionable prompt. See routers/update.py.
    is_dev: bool = False

    # Whether an encrypted backup is configured (BACKUP_ENCRYPTION_KEY set).
    # When False the pre-update snapshot is SKIPPED, so the automatic rollback
    # safety net does not exist — the UI must not promise "your data is never lost".
    backups_configured: bool = False


class CheckResponse(BaseModel):
    """POST /api/update/check result: the outcome and the freshly cached latest.

    status is "ok" or "failed" (the check soft-fails on a GitHub error rather than
    raising). latest_version mirrors the newly cached release, or None on failure.
    """

    status: str
    latest_version: str | None = None


class DismissBody(BaseModel):
    """Body for PUT /api/update/dismiss, the version being dismissed."""

    version: str
