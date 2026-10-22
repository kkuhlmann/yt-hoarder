from typing import Any

from sqlalchemy import and_, asc, desc, func, or_
from sqlmodel import select

from database import db
from logger import logger
from models import Clip, ClipAccess, MediaType
from repositories import media_details as md_repo
from repositories.pagination import page_count
from services.cleanup import delete_file

# --- Async functions for FastAPI ---


async def add_clip(clip: Clip) -> Clip:
    async with db.get_async_session() as session:
        session.add(clip)
        await session.commit()
        await session.refresh(clip)
        return clip


async def get_clip_by_id(id: int) -> Clip | None:
    async with db.get_async_session() as session:
        stmt = select(Clip).where(Clip.id == id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_clips_by_media_details_id(
    media_details_id: int, user_id: int | None = None
) -> list[Clip]:
    """Get all clips for a specific media item.

    Args:
        user_id: When provided, filter to clips owned by or shared with this user.
                 When None, no user filter (admin view).
    """
    async with db.get_async_session() as session:
        conditions = [Clip.media_details_id == media_details_id]

        if user_id is not None:
            accessible_ids = select(ClipAccess.clip_id).where(ClipAccess.user_id == user_id)
            conditions.append(or_(Clip.user_id == user_id, Clip.id.in_(accessible_ids)))

        stmt = select(Clip).where(and_(*conditions)).order_by(desc(Clip.created_at))
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_all_clips(
    search: str | None = None,
    media_type: str | None = None,
    page: int = 1,
    page_size: int = 25,
    sort_by: str | None = None,
    sort_direction: str = 'desc',
    user_id: int | None = None,
) -> dict[str, int | list[dict[str, Any]]]:
    """Get all clips with optional filtering and pagination.

    Args:
        user_id: When provided, filter to clips owned by or shared with this user.
                 When None, no user filter (admin view).
    """
    async with db.get_async_session() as session:
        stmt = select(Clip)
        conditions = []

        if user_id is not None:
            accessible_ids = select(ClipAccess.clip_id).where(ClipAccess.user_id == user_id)
            conditions.append(or_(Clip.user_id == user_id, Clip.id.in_(accessible_ids)))

        if media_type:
            conditions.append(Clip.media_type == media_type)

        if search:
            conditions.append(
                or_(
                    Clip.title.ilike(f'%{search}%'),
                    Clip.description.ilike(f'%{search}%'),
                    Clip.source_title.ilike(f'%{search}%'),
                    Clip.source_channel.ilike(f'%{search}%'),
                )
            )

        if conditions:
            stmt = stmt.where(and_(*conditions))

        count_stmt = select(func.count()).select_from(
            select(Clip.id).where(and_(*conditions) if conditions else True).subquery()
        )
        count_result = await session.execute(count_stmt)
        count_records = count_result.scalar()

        if sort_by and hasattr(Clip, sort_by):
            sort_column = getattr(Clip, sort_by)
            if sort_direction == 'asc':
                stmt = stmt.order_by(asc(sort_column).nullsfirst(), Clip.id.asc())
            else:
                stmt = stmt.order_by(desc(sort_column).nullslast(), Clip.id.desc())
        else:
            stmt = stmt.order_by(Clip.id.desc())

        if page_size is not None:
            stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        result = await session.execute(stmt)
        records = result.scalars().all()
        serialized_records = [record.model_dump(mode='json') for record in records]

    await _add_source_info(serialized_records)
    return {
        'count_records': count_records,
        'page_count': page_count(count_records, page_size),
        'records': serialized_records,
    }


async def _add_source_info(records: list[dict[str, Any]]) -> None:
    """Annotate serialized clips with their source's URL and whether the app holds its file.

    A clip whose source has no file (fetched from YouTube, or whose source was deleted
    since) jumps to the source URL rather than to a local stream that doesn't exist.
    """
    source_ids = {r['media_details_id'] for r in records if r['media_details_id'] is not None}
    sources = {m.id: m for m in await md_repo.get_media_details_by_ids(list(source_ids))}
    for record in records:
        source = sources.get(record['media_details_id'])
        record['source_url'] = source.url if source else None
        record['source_playable'] = bool(source and md_repo.has_playable_file(source))


async def update_clip(id: int, updated_params: dict) -> Clip | None:
    async with db.get_async_session() as session:
        stmt = select(Clip).where(Clip.id == id)
        result = await session.execute(stmt)
        clip = result.scalar_one_or_none()

        if not clip:
            return None

        for key, value in updated_params.items():
            if hasattr(clip, key):
                setattr(clip, key, value)

        await session.commit()
        await session.refresh(clip)
        return clip


async def delete_clip_by_id(id: int) -> int:
    """Delete a Clip record by ID and its associated file.

    Returns:
        Number of records deleted (0 or 1)
    """
    async with db.get_async_session() as session:
        stmt = select(Clip).where(Clip.id == id)
        result = await session.execute(stmt)
        clip = result.scalar_one_or_none()

        if not clip:
            return 0

        delete_file(clip.file_path)
        logger.info(f'Deleted clip file: {clip.file_path}')

        await session.delete(clip)
        await session.commit()
        logger.info(f'Deleted Clip with id: {id}')
        return 1


async def bulk_delete_clips(clip_ids: list[int], user_id: int, is_admin: bool) -> dict:
    """Delete multiple clips in a single transaction.

    Owner/admin clips are hard-deleted (file + row; ClipAccess cascades via FK). For
    clips the user only has shared access to, their own ClipAccess row is removed. Clips
    the user cannot access are ignored. Returns per-outcome counts.
    """
    if not clip_ids:
        return {'deleted_count': 0, 'access_removed': 0, 'not_found': 0, 'errors': []}

    async with db.get_async_session() as session:
        clips = list((await session.execute(select(Clip).where(Clip.id.in_(clip_ids)))).scalars())
        found_ids = {c.id for c in clips}
        not_found = len([cid for cid in set(clip_ids) if cid not in found_ids])

        # Shared-access rows this user holds among the requested clips
        shared_access = {
            row.clip_id: row
            for row in (
                await session.execute(
                    select(ClipAccess).where(
                        and_(ClipAccess.user_id == user_id, ClipAccess.clip_id.in_(clip_ids))
                    )
                )
            ).scalars()
        }

        deleted_count = 0
        access_removed = 0
        for clip in clips:
            if clip.user_id == user_id or is_admin:
                delete_file(clip.file_path)
                await session.delete(clip)
                deleted_count += 1
            elif clip.id in shared_access:
                await session.delete(shared_access[clip.id])
                access_removed += 1
            # else: user has no access to this clip → ignore

        await session.commit()
        logger.info(
            f'Bulk-deleted {deleted_count} clips; removed {access_removed} shared-access rows'
        )
        return {
            'deleted_count': deleted_count,
            'access_removed': access_removed,
            'not_found': not_found,
            'errors': [],
        }


async def get_clip_stats(user_id: int | None = None) -> dict:
    """Get clip statistics.

    Args:
        user_id: When provided, count only clips owned by or shared with this user.
                 When None, count all clips (admin view).

    Returns:
        dict with total_clips, audio_clips, video_clips
    """
    async with db.get_async_session() as session:

        def _user_filter():
            if user_id is None:
                return []
            accessible_ids = select(ClipAccess.clip_id).where(ClipAccess.user_id == user_id)
            return [or_(Clip.user_id == user_id, Clip.id.in_(accessible_ids))]

        total_stmt = select(func.count()).select_from(Clip).where(*_user_filter())
        total_result = await session.execute(total_stmt)
        total_clips = total_result.scalar() or 0

        audio_stmt = (
            select(func.count())
            .select_from(Clip)
            .where(Clip.media_type == MediaType.AUDIO, *_user_filter())
        )
        audio_result = await session.execute(audio_stmt)
        audio_clips = audio_result.scalar() or 0

        video_stmt = (
            select(func.count())
            .select_from(Clip)
            .where(Clip.media_type == MediaType.VIDEO, *_user_filter())
        )
        video_result = await session.execute(video_stmt)
        video_clips = video_result.scalar() or 0

        return {
            'total_clips': total_clips,
            'audio_clips': audio_clips,
            'video_clips': video_clips,
        }


# --- Sync functions (job bodies run in lane threads / the ML child) ---


def sync_get_clip_by_id(id: int) -> Clip | None:
    with db.sync_session() as session:
        stmt = select(Clip).where(Clip.id == id)
        result = session.execute(stmt)
        return result.scalar_one_or_none()


def sync_update_clip(id: int, updated_params: dict) -> Clip | None:
    with db.sync_session() as session:
        stmt = select(Clip).where(Clip.id == id)
        result = session.execute(stmt)
        clip = result.scalar_one_or_none()

        if not clip:
            return None

        for key, value in updated_params.items():
            if hasattr(clip, key):
                setattr(clip, key, value)

        session.flush()
        session.refresh(clip)
        return clip
