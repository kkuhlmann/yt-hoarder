from sqlalchemy import and_, delete, func, or_
from sqlmodel import select

from database import db
from logger import logger
from models import (
    DownloadJob,
    MediaAccess,
    MediaType,
    SourceType,
    Subscription,
    SubscriptionAccess,
)
from repositories.pagination import page_count

_MEDIA_ONLY_SUBSCRIPTION_FIELDS = frozenset(
    {'audio_only', 'generate_transcript', 'download_quality', 'audio_quality'}
)

# --- Async functions for FastAPI ---


async def delete_subscription(id: int) -> int:
    """Delete a subscription by ID.

    Note: Related download_jobs are cascade-deleted via FK constraint.
    Also cleans up all subscription-sourced MediaAccess rows atomically.
    """
    async with db.get_async_session() as session:
        stmt = select(Subscription).where(Subscription.id == id)
        result = await session.execute(stmt)
        subscription = result.scalar_one_or_none()

        if not subscription:
            return 0

        cleanup_stmt = delete(MediaAccess).where(
            MediaAccess.source_type == SourceType.SUBSCRIPTION,
            MediaAccess.source_id == id,
        )
        cleanup_result = await session.execute(cleanup_stmt)
        if cleanup_result.rowcount > 0:
            logger.info(
                f'Cleaned up {cleanup_result.rowcount} subscription-sourced media_access rows '
                f'for subscription {id}'
            )

        # Clean up SubscriptionAccess rows (normally CASCADE'd by FK, but explicit for safety)
        sa_cleanup = delete(SubscriptionAccess).where(SubscriptionAccess.subscription_id == id)
        await session.execute(sa_cleanup)

        await session.delete(subscription)
        await session.commit()
        logger.debug(f'Deleted subscription with ID: {id}')
        return 1


async def get_subscription_by_id(id: int) -> Subscription | None:
    async with db.get_async_session() as session:
        stmt = select(Subscription).where(Subscription.id == id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_subscription_by_details(
    url: str, string_match: str | None, media_type: MediaType, user_id: int | None
) -> Subscription | None:
    async with db.get_async_session() as session:
        conditions = [
            Subscription.url == url,
            Subscription.user_id == user_id,
            Subscription.media_type == media_type,
        ]

        if string_match and str(string_match).strip() != '':
            conditions.append(func.lower(Subscription.string_match) == func.lower(string_match))
        else:
            conditions.append(
                or_(Subscription.string_match.is_(None), Subscription.string_match == '')
            )

        stmt = select(Subscription).where(and_(*conditions))
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_all_subscriptions(
    job_type: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
    user_id: int | None = None,
) -> dict:
    """Get all subscriptions with optional filtering and pagination.

    Args:
        user_id: When provided, filter to subscriptions owned by this user OR
                 shared via SubscriptionAccess. When None, no user filter (admin view).
    """
    async with db.get_async_session() as session:
        stmt = select(Subscription)
        conditions = []

        if user_id is not None:
            accessible_ids = select(SubscriptionAccess.subscription_id).where(
                SubscriptionAccess.user_id == user_id
            )
            conditions.append(
                or_(Subscription.user_id == user_id, Subscription.id.in_(accessible_ids))
            )

        if job_type:
            conditions.append(Subscription.job_type == job_type)

        if search:
            conditions.append(
                or_(
                    Subscription.channel.ilike(f'%{search}%'),
                    Subscription.string_match.ilike(f'%{search}%'),
                )
            )

        if conditions:
            stmt = stmt.where(and_(*conditions))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        count_result = await session.execute(count_stmt)
        count_records = count_result.scalar()

        stmt = stmt.order_by(Subscription.id.desc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        result = await session.execute(stmt)
        records = result.scalars().all()

        # Convert to dicts with proper enum serialization
        serialized_records = [record.model_dump(mode='json') for record in records]

        return {
            'count_records': count_records,
            'page_count': page_count(count_records, page_size),
            'records': serialized_records,
        }


async def update_subscription(id: int, params: dict) -> Subscription:
    async with db.get_async_session() as session:
        stmt = select(Subscription).where(Subscription.id == id)
        result = await session.execute(stmt)
        subscription = result.scalar_one_or_none()

        if not subscription:
            msg = f'Subscription with ID: {id} was not updated'
            raise ValueError(msg)

        allowed_fields = [
            'string_match',
            'enabled',
            'audio_only',
            'overwrite',
            'channel',
            'generate_transcript',
            'download_quality',
            'audio_quality',
            'min_duration_seconds',
            'max_duration_seconds',
        ]
        # A transcript-only subscription's mode is fixed at creation: these would turn it
        # into a media subscription without changing what it indexes.
        if subscription.media_type == MediaType.TRANSCRIPT:
            allowed_fields = [f for f in allowed_fields if f not in _MEDIA_ONLY_SUBSCRIPTION_FIELDS]
        for field in allowed_fields:
            if field in params:
                setattr(subscription, field, params[field])
        # media_type follows audio_only, or downloads of one format land under the other's
        # media type and the subscription's unique key stops meaning anything.
        if 'audio_only' in allowed_fields and 'audio_only' in params:
            subscription.media_type = (
                MediaType.AUDIO if subscription.audio_only else MediaType.VIDEO
            )

        await session.commit()
        await session.refresh(subscription)
        return subscription


async def get_subscription_media_ids(subscription_id: int) -> list[int]:
    async with db.get_async_session() as session:
        stmt = select(func.distinct(DownloadJob.media_details_id)).where(
            DownloadJob.subscription_id == subscription_id,
            DownloadJob.media_details_id.is_not(None),
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]


# --- Sync functions (job bodies run in lane threads / the ML child) ---


def sync_get_enabled_subscriptions(job_type: str | None = None) -> list[Subscription]:
    with db.sync_session() as session:
        stmt = select(Subscription).where(Subscription.enabled)
        if job_type:
            stmt = stmt.where(Subscription.job_type == job_type)

        result = session.execute(stmt)
        return list(result.scalars().all())


def sync_add_subscription(subscription: Subscription) -> Subscription:
    if subscription.date_filter and subscription.date_filter.tzinfo is not None:
        subscription.date_filter = subscription.date_filter.replace(tzinfo=None)

    with db.sync_session() as session:
        session.add(subscription)
        session.flush()
        session.refresh(subscription)
        logger.debug(f'created_subscription: {subscription}')
        return subscription
