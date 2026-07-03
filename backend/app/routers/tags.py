from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.tag import (
    HoldingTagApply,
    TagCreate,
    TagResponse,
    TagsResponse,
)
from app.services.tags import (
    DuplicateTagError,
    apply_tag_to_holding,
    create_tag,
    delete_tag,
    list_tags_with_counts,
    remove_tag_from_holding,
)

tags_router = APIRouter(prefix="/api/tags", tags=["tags"])
holding_tags_router = APIRouter(prefix="/api/holdings", tags=["tags"])


@tags_router.get("", response_model=TagsResponse)
async def get_tags(db: AsyncSession = Depends(get_db)):
    rows = await list_tags_with_counts(db)
    return TagsResponse(
        tags=[
            TagResponse(id=tag.id, name=tag.name, holdings_count=count)
            for tag, count in rows
        ]
    )


@tags_router.post("", response_model=TagResponse, status_code=201)
async def post_tag(body: TagCreate, db: AsyncSession = Depends(get_db)):
    try:
        row = await create_tag(db, body.name)
    except DuplicateTagError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Duplicate tag name.") from exc
    return TagResponse.model_validate(row)


@tags_router.delete("/{tag_id}", status_code=204)
async def delete_tag_route(
    tag_id: str = Path(..., max_length=64),
    db: AsyncSession = Depends(get_db),
):
    deleted = await delete_tag(db, tag_id)
    await db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Tag not found.")


@holding_tags_router.post(
    "/{account_id}/{instrument_id}/tags", status_code=204
)
async def apply_holding_tag(
    body: HoldingTagApply,
    account_id: str = Path(..., max_length=64),
    instrument_id: str = Path(..., max_length=64),
    db: AsyncSession = Depends(get_db),
):
    try:
        await apply_tag_to_holding(db, account_id, instrument_id, body.tag_id)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=422,
            detail="Unknown account, instrument, or tag id.",
        ) from exc


@holding_tags_router.delete(
    "/{account_id}/{instrument_id}/tags/{tag_id}", status_code=204
)
async def remove_holding_tag(
    account_id: str = Path(..., max_length=64),
    instrument_id: str = Path(..., max_length=64),
    tag_id: str = Path(..., max_length=64),
    db: AsyncSession = Depends(get_db),
):
    await remove_tag_from_holding(db, account_id, instrument_id, tag_id)
    await db.commit()
