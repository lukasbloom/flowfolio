"""Tag and holding-tag CRUD service.

Caller-commits contract (mirrors backend/app/services/fifo.py):
    Must be called INSIDE an open DB transaction (caller is responsible for
    commit). Services stage rows via session.add(...); routers commit.

Additional helpers:
    - list_tags_with_counts: enrich GET /api/tags with holdings_count (cascade
      preview UX in TagsManager delete confirmation).
    - list_holdings_for_instrument: per-instrument holdings + currently attached
      tags for the HoldingTagsEditor popover and the instrument page Tags section.
"""
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.tag import HoldingTag, Tag
from app.models.transaction import Transaction


class DuplicateTagError(Exception):
    """Raised when POST /api/tags receives a name that already exists."""


async def list_tags(session: AsyncSession) -> list[Tag]:
    result = await session.execute(select(Tag).order_by(Tag.name.asc()))
    return list(result.scalars())


async def list_tags_with_counts(
    session: AsyncSession,
) -> list[tuple[Tag, int]]:
    """Return (Tag, holdings_count) pairs sorted by Tag.name ASC.

    holdings_count = number of distinct (account_id, instrument_id) HoldingTag
    rows pointing at the tag. Soft-deleted transactions do NOT affect this
    count — tags bind to the holding-pair identity in the holding_tag table,
    not to live transaction history.
    """
    stmt = (
        select(Tag, func.count(HoldingTag.tag_id).label("holdings_count"))
        .outerjoin(HoldingTag, HoldingTag.tag_id == Tag.id)
        .group_by(Tag.id)
        .order_by(Tag.name.asc())
    )
    result = await session.execute(stmt)
    return [(row[0], int(row[1] or 0)) for row in result.all()]


async def create_tag(session: AsyncSession, name: str) -> Tag:
    # Check duplicate first to give a clean 409 (vs IntegrityError surfaced by commit)
    existing = await session.execute(select(Tag).where(Tag.name == name))
    if existing.scalar_one_or_none() is not None:
        raise DuplicateTagError(f"Tag with name {name!r} already exists.")
    tag = Tag(name=name)
    session.add(tag)
    await session.flush()  # populate id
    return tag


async def delete_tag(session: AsyncSession, tag_id: str) -> bool:
    # Manually cascade holding_tag rows (no FK ON DELETE CASCADE on holding_tag.tag_id today)
    await session.execute(delete(HoldingTag).where(HoldingTag.tag_id == tag_id))
    result = await session.execute(delete(Tag).where(Tag.id == tag_id))
    return (result.rowcount or 0) > 0


async def apply_tag_to_holding(
    session: AsyncSession, account_id: str, instrument_id: str, tag_id: str
) -> None:
    """Idempotent: re-applying the same (account, instrument, tag) is a no-op."""
    existing = await session.execute(
        select(HoldingTag).where(
            HoldingTag.account_id == account_id,
            HoldingTag.instrument_id == instrument_id,
            HoldingTag.tag_id == tag_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return
    session.add(
        HoldingTag(account_id=account_id, instrument_id=instrument_id, tag_id=tag_id)
    )


async def remove_tag_from_holding(
    session: AsyncSession, account_id: str, instrument_id: str, tag_id: str
) -> bool:
    result = await session.execute(
        delete(HoldingTag).where(
            HoldingTag.account_id == account_id,
            HoldingTag.instrument_id == instrument_id,
            HoldingTag.tag_id == tag_id,
        )
    )
    return (result.rowcount or 0) > 0


async def list_holdings_for_instrument(
    session: AsyncSession, instrument_id: str
) -> list[dict]:
    """Return per-account holdings for an instrument with attached tags.

    A "holding" exists if any non-deleted Transaction row has the
    (account_id, instrument_id) pair (matches perf.py:253-271 idiom). The
    function does NOT require open quantity > 0 — closed positions still
    carry their tag bindings, and the UI renders editors for every holding
    the user has ever recorded. Result sorted by account_name ASC for
    deterministic UI ordering.

    Returns a list of plain dicts with keys: account_id, account_name, tags.
    Each tag entry is a dict {id, name, holdings_count} matching TagResponse
    (holdings_count is set to 0 here because a per-instrument response should
    not require the FE to disambiguate "global tag count" vs "this-instrument
    tag count" — it's purely an additive default to keep one shape).
    """
    # Discover holdings (account_ids) for this instrument from non-deleted txns.
    holding_stmt = (
        select(Transaction.account_id)
        .where(Transaction.deleted_at.is_(None))
        .where(Transaction.instrument_id == instrument_id)
        .group_by(Transaction.account_id)
    )
    holding_result = await session.execute(holding_stmt)
    account_ids = [row[0] for row in holding_result.all()]
    if not account_ids:
        return []

    # Fetch account names (one round-trip).
    acct_result = await session.execute(
        select(Account.id, Account.name).where(Account.id.in_(account_ids))
    )
    account_name_by_id = {row[0]: row[1] for row in acct_result.all()}

    # Fetch every (account_id, tag) pair scoped to this instrument
    # (one query, server-side join).
    tag_stmt = (
        select(
            HoldingTag.account_id,
            Tag.id,
            Tag.name,
        )
        .join(Tag, Tag.id == HoldingTag.tag_id)
        .where(HoldingTag.instrument_id == instrument_id)
        .where(HoldingTag.account_id.in_(account_ids))
        .order_by(Tag.name.asc())
    )
    tag_result = await session.execute(tag_stmt)
    tags_by_account: dict[str, list[dict]] = {acct_id: [] for acct_id in account_ids}
    for row in tag_result.all():
        acct_id, tag_id, tag_name = row
        tags_by_account[acct_id].append(
            {"id": tag_id, "name": tag_name, "holdings_count": 0}
        )

    # Sort accounts by name ASC for deterministic UI render order.
    sorted_account_ids = sorted(
        account_ids,
        key=lambda aid: (account_name_by_id.get(aid) or "").lower(),
    )

    return [
        {
            "account_id": aid,
            "account_name": account_name_by_id.get(aid, ""),
            "tags": tags_by_account.get(aid, []),
        }
        for aid in sorted_account_ids
    ]
