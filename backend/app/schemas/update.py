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

    # Whether an encrypted backup is configured (BACKUP_ENCRYPTION_KEY set).
    # When False the pre-update snapshot is SKIPPED, so the automatic rollback
    # safety net does not exist — the UI must not promise "your data is never lost".
    backups_configured: bool = False

    # Live updater progress merged from the shared-volume status.json.
    # Idle when no update is running (in_progress False, the rest None).
    update_in_progress: bool = False
    update_state: str | None = None
    update_message: str | None = None
    update_log_tail: str | None = None


class DismissBody(BaseModel):
    """Body for PUT /api/update/dismiss, the version being dismissed."""

    version: str


class ApplyResponse(BaseModel):
    """POST /api/update/apply returns the run id + the updater's current state.

    request_id is the lock key: a re-click during a non-terminal run returns the
    SAME id (re-attach) rather than starting a second recreate.
    """

    request_id: str
    state: str | None = None
