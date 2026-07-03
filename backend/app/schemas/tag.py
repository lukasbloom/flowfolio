from pydantic import BaseModel, ConfigDict, Field


class TagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    # Optional holdings count for cascade-preview UX in TagsManager
    # delete confirmation. Backwards-additive: clients that ignore this field
    # (e.g. existing TagFilterChip) continue to work unchanged.
    holdings_count: int = 0


class TagsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tags: list[TagResponse]


class TagCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(min_length=1, max_length=64)


class HoldingTagApply(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tag_id: str = Field(min_length=1, max_length=64)


class InstrumentHoldingResponse(BaseModel):
    """One (account, instrument) pair the user holds, with currently-attached tags.

    Returned by GET /api/instruments/{instrument_id}/holdings as a list element.
    The frontend renders one HoldingTagsEditor
    per element so a user holding the same instrument in two accounts gets two
    editors (matching the data model — tags bind to (account_id, instrument_id)).
    """

    model_config = ConfigDict(from_attributes=True)

    account_id: str
    account_name: str
    tags: list[TagResponse]
