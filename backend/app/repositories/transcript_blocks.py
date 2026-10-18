from sqlalchemy import and_, delete, text
from sqlmodel import select

from database import db
from logger import logger
from models import TranscriptBlock

# --- Async functions for FastAPI ---


async def delete_transcript_block_by_media_details_id(media_details_id: int) -> int:
    """Delete all transcript blocks for a given media_details_id."""
    async with db.get_async_session() as session:
        stmt = delete(TranscriptBlock).where(TranscriptBlock.media_details_id == media_details_id)
        result = await session.execute(stmt)
        deleted_count = result.rowcount
        await session.commit()
        logger.debug(
            f'Deleted {deleted_count} transcript blocks for media_details.id: {media_details_id}'
        )
        return deleted_count


async def get_blocks_in_window(
    media_details_id: int, start: float, end: float | None, limit: int
) -> list[TranscriptBlock]:
    """Blocks of one media overlapping [start, end), in playback order.

    `end=None` reads to the end of the transcript. Access is the caller's to check.
    """
    conditions = [
        TranscriptBlock.media_details_id == media_details_id,
        TranscriptBlock.end_time > start,
    ]
    if end is not None:
        conditions.append(TranscriptBlock.start_time < end)
    async with db.get_async_session() as session:
        stmt = (
            select(TranscriptBlock)
            .where(and_(*conditions))
            .order_by(TranscriptBlock.start_time, TranscriptBlock.id)
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


# --- Sync functions (job bodies run in lane threads / the ML child) ---


def sync_delete_transcript_block_by_media_details_id(media_details_id: int) -> int:
    """Sync version: Delete all transcript blocks for a given media_details_id.

    Embeddings are cleaned up automatically via ON DELETE CASCADE on the
    transcript_embeddings foreign key.
    """
    with db.sync_session() as session:
        del_stmt = delete(TranscriptBlock).where(
            TranscriptBlock.media_details_id == media_details_id
        )
        result = session.execute(del_stmt)
        deleted_count = result.rowcount
        logger.debug(
            f'Deleted {deleted_count} transcript blocks (+ cascaded embeddings) '
            f'for media_details.id: {media_details_id}'
        )
        return deleted_count


def sync_has_transcript_blocks(media_details_id: int) -> bool:
    """Check if any transcript blocks exist for this media.

    More efficient than fetching all blocks - uses LIMIT 1 query.
    """
    if not media_details_id:
        return False
    with db.sync_session() as session:
        stmt = (
            select(TranscriptBlock.id)
            .where(TranscriptBlock.media_details_id == media_details_id)
            .limit(1)
        )
        result = session.execute(stmt)
        return result.scalar_one_or_none() is not None


def sync_replace_transcript_blocks_with_embeddings(
    media_details_id: int,
    blocks: list[TranscriptBlock],
    embeddings_data: list[dict],
) -> list[int]:
    """Atomically replace transcript blocks and embeddings for a media item.

    Performs delete-old → insert-blocks → insert-embeddings in a single
    session/transaction so the operation either fully succeeds or fully rolls back.

    Args:
        media_details_id: The media_details ID whose blocks are being replaced.
        blocks: New TranscriptBlock instances to insert (without IDs yet).
        embeddings_data: List of dicts with 'embedding' key (pgvector string).
            Must be same length and order as blocks.

    Returns:
        List of inserted block IDs.
    """
    with db.sync_session() as session:
        # 1. Delete existing blocks (embeddings cascade automatically)
        del_stmt = delete(TranscriptBlock).where(
            TranscriptBlock.media_details_id == media_details_id
        )
        result = session.execute(del_stmt)
        deleted_count = result.rowcount
        logger.debug(
            f'Deleted {deleted_count} old transcript blocks for media_details.id: {media_details_id}'
        )

        if not blocks:
            return []

        # 2. Insert new blocks and flush to populate IDs
        session.add_all(blocks)
        session.flush()
        inserted_ids = [block.id for block in blocks]

        # 3. Batch-insert embeddings via executemany
        if embeddings_data:
            embedding_rows = [
                {'id': block_id, 'emb': emb_data['embedding']}
                for block_id, emb_data in zip(inserted_ids, embeddings_data, strict=False)
            ]
            session.execute(
                text(
                    'INSERT INTO transcript_embeddings (transcript_block_id, embedding) '
                    'VALUES (:id, :emb)'
                ),
                embedding_rows,
            )

        logger.info(
            f'Inserted {len(inserted_ids)} transcript blocks and embeddings '
            f'for media_details.id: {media_details_id}'
        )
        return inserted_ids
