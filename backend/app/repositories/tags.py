from sqlalchemy import and_, delete, func
from sqlmodel import select

from database import db
from models import MediaDetails, MediaTag, Tag, TaskStatus


async def get_user_tags(user_id: int) -> list[dict]:
    """Get a user's tags that still have at least one COMPLETE media, with usage counts.

    Tags whose media were all deleted (cascade-cleaned) or untagged are excluded, so the
    Media Library Tags dropdown only offers tags that will actually return results.
    """
    async with db.get_async_session() as session:
        stmt = (
            select(
                Tag,
                func.count(func.distinct(MediaTag.media_details_id)).label('usage_count'),
            )
            .join(MediaTag, Tag.id == MediaTag.tag_id)
            .join(MediaDetails, MediaTag.media_details_id == MediaDetails.id)
            .where(
                and_(
                    Tag.user_id == user_id,
                    MediaTag.user_id == user_id,
                    MediaDetails.status == TaskStatus.COMPLETE,
                )
            )
            .group_by(Tag.id)
            .order_by(Tag.name)
        )
        result = await session.execute(stmt)
        return [
            {
                'id': tag.id,
                'name': tag.name,
                'usage_count': count,
                'created_at': tag.created_at.isoformat() if tag.created_at else None,
            }
            for tag, count in result.all()
        ]


async def find_tags_by_name(user_id: int, names: list[str]) -> list[Tag]:
    """The user's tags whose names match any of `names`, ignoring case."""
    lowered = sorted({name.strip().lower() for name in names if name.strip()})
    if not lowered:
        return []
    async with db.get_async_session() as session:
        stmt = select(Tag).where(Tag.user_id == user_id, func.lower(Tag.name).in_(lowered))
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_or_create_tag(user_id: int, name: str) -> Tag:
    """Get existing tag or create new one. Name is normalized (stripped, lowercased)."""
    normalized = name.strip().lower()
    async with db.get_async_session() as session:
        stmt = select(Tag).where(and_(Tag.user_id == user_id, Tag.name == normalized))
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        tag = Tag(user_id=user_id, name=normalized)
        session.add(tag)
        await session.commit()
        await session.refresh(tag)
        return tag


async def rename_tag(user_id: int, tag_id: int, new_name: str) -> Tag | None:
    """Rename a tag. Returns None if not found or not owned by user."""
    normalized = new_name.strip().lower()
    async with db.get_async_session() as session:
        stmt = select(Tag).where(and_(Tag.id == tag_id, Tag.user_id == user_id))
        result = await session.execute(stmt)
        tag = result.scalar_one_or_none()
        if not tag:
            return None

        collision_stmt = select(Tag).where(
            and_(Tag.user_id == user_id, Tag.name == normalized, Tag.id != tag_id)
        )
        collision_result = await session.execute(collision_stmt)
        if collision_result.scalar_one_or_none():
            return None

        tag.name = normalized
        await session.commit()
        await session.refresh(tag)
        return tag


async def delete_tag(user_id: int, tag_id: int) -> bool:
    """Delete a tag and all its associations. Returns True if deleted."""
    async with db.get_async_session() as session:
        await session.execute(
            delete(MediaTag).where(and_(MediaTag.tag_id == tag_id, MediaTag.user_id == user_id))
        )
        stmt = delete(Tag).where(and_(Tag.id == tag_id, Tag.user_id == user_id))
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount > 0


async def set_media_tags(user_id: int, media_details_id: int, tag_names: list[str]) -> list[dict]:
    """Set tags for a media item. Creates missing tags, removes absent ones.

    Returns the final list of tags on this media item.
    """
    normalized_names = [n.strip().lower() for n in tag_names if n.strip()]
    seen = set()
    unique_names = []
    for name in normalized_names:
        if name not in seen:
            seen.add(name)
            unique_names.append(name)

    async with db.get_async_session() as session:
        tag_ids = []
        for name in unique_names:
            stmt = select(Tag).where(and_(Tag.user_id == user_id, Tag.name == name))
            result = await session.execute(stmt)
            tag = result.scalar_one_or_none()
            if not tag:
                tag = Tag(user_id=user_id, name=name)
                session.add(tag)
                await session.flush()
            tag_ids.append(tag.id)

        await session.execute(
            delete(MediaTag).where(
                and_(MediaTag.user_id == user_id, MediaTag.media_details_id == media_details_id)
            )
        )

        for tag_id in tag_ids:
            session.add(MediaTag(user_id=user_id, media_details_id=media_details_id, tag_id=tag_id))

        await session.commit()

        tags_stmt = (
            select(Tag)
            .join(MediaTag, Tag.id == MediaTag.tag_id)
            .where(and_(MediaTag.user_id == user_id, MediaTag.media_details_id == media_details_id))
            .order_by(Tag.name)
        )
        result = await session.execute(tags_stmt)
        return [{'id': t.id, 'name': t.name} for t in result.scalars().all()]


async def add_tags_to_media_bulk(
    user_id: int, media_details_ids: list[int], tag_names: list[str]
) -> dict:
    """Add the given tags to multiple media items for a user (union with existing).

    Creates missing tags on the fly. Idempotent: existing (user, media, tag)
    associations are left untouched. Runs in a single transaction.
    Returns {'tagged_count', 'associations_added'}.
    """
    normalized_names = [n.strip().lower() for n in tag_names if n.strip()]
    seen_names: set[str] = set()
    unique_names = [n for n in normalized_names if not (n in seen_names or seen_names.add(n))]

    seen_media: set[int] = set()
    unique_media_ids = [m for m in media_details_ids if not (m in seen_media or seen_media.add(m))]

    if not unique_names or not unique_media_ids:
        return {'tagged_count': 0, 'associations_added': 0}

    async with db.get_async_session() as session:
        tag_ids: list[int] = []
        for name in unique_names:
            tag = (
                await session.execute(
                    select(Tag).where(and_(Tag.user_id == user_id, Tag.name == name))
                )
            ).scalar_one_or_none()
            if not tag:
                tag = Tag(user_id=user_id, name=name)
                session.add(tag)
                await session.flush()
            tag_ids.append(tag.id)

        # Only tag media that actually exist (avoid FK errors on stale ids)
        valid_media_ids = set(
            (
                await session.execute(
                    select(MediaDetails.id).where(MediaDetails.id.in_(unique_media_ids))
                )
            ).scalars()
        )
        target_media_ids = [m for m in unique_media_ids if m in valid_media_ids]

        # Existing (media, tag) associations for this user among the targets
        existing_pairs = set()
        if target_media_ids:
            rows = (
                await session.execute(
                    select(MediaTag.media_details_id, MediaTag.tag_id).where(
                        and_(
                            MediaTag.user_id == user_id,
                            MediaTag.media_details_id.in_(target_media_ids),
                            MediaTag.tag_id.in_(tag_ids),
                        )
                    )
                )
            ).all()
            existing_pairs = {(m, t) for m, t in rows}

        added = 0
        for media_id in target_media_ids:
            for tag_id in tag_ids:
                if (media_id, tag_id) not in existing_pairs:
                    session.add(MediaTag(user_id=user_id, media_details_id=media_id, tag_id=tag_id))
                    added += 1

        await session.commit()
        return {'tagged_count': len(target_media_ids), 'associations_added': added}


async def get_tags_for_media_ids(user_id: int, media_ids: list[int]) -> dict[int, list[dict]]:
    """Batch fetch tags for multiple media items. Returns dict[media_id, list[tag]]."""
    if not media_ids:
        return {}

    async with db.get_async_session() as session:
        stmt = (
            select(MediaTag.media_details_id, Tag.id, Tag.name)
            .join(Tag, MediaTag.tag_id == Tag.id)
            .where(and_(MediaTag.user_id == user_id, MediaTag.media_details_id.in_(media_ids)))
            .order_by(Tag.name)
        )
        result = await session.execute(stmt)

        tags_by_media: dict[int, list[dict]] = {}
        for media_id, tag_id, tag_name in result.all():
            tags_by_media.setdefault(media_id, []).append({'id': tag_id, 'name': tag_name})
        return tags_by_media


async def remove_all_media_tags_for_media(media_details_id: int) -> int:
    """Remove ALL MediaTag rows for a media (all users).

    Used on soft delete so tags don't linger on media that has left every library.
    Returns the number of rows deleted.
    """
    async with db.get_async_session() as session:
        result = await session.execute(
            delete(MediaTag).where(MediaTag.media_details_id == media_details_id)
        )
        await session.commit()
        return result.rowcount


async def remove_user_media_tags_for_media(user_id: int, media_details_id: int) -> int:
    """Remove a single user's MediaTag rows for a media.

    Used when a non-owner removes media from their own library (direct-access delete).
    Returns the number of rows deleted.
    """
    async with db.get_async_session() as session:
        result = await session.execute(
            delete(MediaTag).where(
                and_(
                    MediaTag.user_id == user_id,
                    MediaTag.media_details_id == media_details_id,
                )
            )
        )
        await session.commit()
        return result.rowcount
