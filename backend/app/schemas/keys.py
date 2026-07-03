"""Keys API schemas — the masked status surface and the write body.

`ProviderStatus` carries the registry metadata plus a masked status; there is
deliberately NO raw-value field, so a stored key can never serialize back to the
client. `KeysResponse` wraps the demo flag + the ordered list.
`KeyUpdate` mirrors `SettingUpdate` — a single candidate value.
"""
from pydantic import BaseModel, ConfigDict


class ProviderStatus(BaseModel):
    """One provider rendered by the wizard / Settings — masked status only."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    blurb: str
    free_tier: str
    register_url: str
    optional: bool
    configured: bool
    masked: str | None = None


class KeysResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    demo: bool
    providers: list[ProviderStatus]


class KeyUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    value: str
