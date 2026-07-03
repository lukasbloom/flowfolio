from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    transaction_id: str
    event_type: str        # 'edit' or 'delete'
    changed_at: datetime
    changed_fields: dict[str, Any]    # for edits: {field: {old, new}}; for deletes: {} or {"deleted_at": {"old": null, "new": "<iso>"}}
