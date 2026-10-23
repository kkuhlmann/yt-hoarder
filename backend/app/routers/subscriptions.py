from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError

from dependencies import (
    get_effective_user_id,
    get_entity_or_404,
    get_required_user_id,
)
from logger import logger
from models import MediaType, Subscription
from orchestrator import ADD_SUBSCRIPTION_JOB, SUBSCRIPTION_CHECK_JOB, JobSpec, orch
from repositories import settings as settings_repo
from repositories import sharing as sharing_repo
from repositories import subscription_access as sa_repo
from repositories import subscriptions as sub_repo
from routers.sharing_routes import SharingConfig, register_sharing_routes
from schemas import SubscriptionDTO, apply_transcript_only_defaults
from serializers import serialize_subscription

router = APIRouter()


@router.get(
    '/{id}',
    status_code=status.HTTP_200_OK,
    response_description='Subscription object',
)
async def get_one_subscription(
    request: Request, id: int, user_id: int = Depends(get_required_user_id)
):
    return await get_entity_or_404(
        sub_repo.get_subscription_by_id,
        id,
        'Subscription',
        access_check=partial(
            sa_repo.check_subscription_access_or_raise, user_id, is_admin=request.state.is_admin
        ),
    )


@router.post(
    '',
    status_code=status.HTTP_201_CREATED,
    response_description='Add Subscription',
    response_model=dict,
)
async def add_subscription(
    subscription: Subscription, user_id: int = Depends(get_required_user_id)
):
    if subscription.string_match == '':
        subscription.string_match = None
    # The table model is bound directly, so no validator ran; media_type is the unique key's
    # discriminator and must be set.
    if subscription.media_type is None:
        subscription.media_type = MediaType.AUDIO if subscription.audio_only else MediaType.VIDEO
    apply_transcript_only_defaults(subscription)

    if (
        sub_check := await sub_repo.get_subscription_by_details(
            url=subscription.url,
            string_match=subscription.string_match,
            media_type=subscription.media_type,
            user_id=user_id,
        )
    ) is None:
        sub_dto = SubscriptionDTO.from_orm(subscription)
        sub_dto.user_id = user_id
        task_id = await orch.submit(
            JobSpec(
                job_name=ADD_SUBSCRIPTION_JOB,
                args=(serialize_subscription(sub_dto),),
                tracked=False,
                priority=0,  # Highest priority (0-9 scale)
                user_id=user_id,
            )
        )
        logger.info({'task': task_id})
        return {'task': task_id}
    logger.warning(f'Duplicate Error: Subscription already exists: {sub_check}')
    return {'task': 'DUPLICATE_SUBSCRIPTION'}


@router.get(
    '',
    status_code=status.HTTP_200_OK,
    response_description='Subscriptions objects matching criteria',
    response_model=dict[str, int | list[dict[str, Any]]],
)
async def get_all_subscriptions(
    search: str | None = None,
    page: int = 1,
    page_size: int | None = None,
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    if page_size is None:
        settings = await settings_repo.get_settings()
        page_size = settings.subscription_table_page_size
    return await sub_repo.get_all_subscriptions(
        search=search, page=page, page_size=page_size, user_id=effective_user_id
    )


@router.delete(
    '/{id}',
    status_code=status.HTTP_204_NO_CONTENT,
    response_description='Delete Subscription by ID',
)
async def delete_subscription(
    request: Request, id: int, user_id: int = Depends(get_required_user_id)
):
    subscription = await get_entity_or_404(
        sub_repo.get_subscription_by_id,
        id,
        'Subscription',
        access_check=partial(
            sa_repo.check_subscription_access_or_raise, user_id, is_admin=request.state.is_admin
        ),
    )

    if subscription.user_id == user_id or request.state.is_admin:
        await sub_repo.delete_subscription(id)
        return

    removed, revoked = await sharing_repo.unshare_subscription_for_user(user_id, id)
    if removed:
        if revoked > 0:
            logger.info(
                f'Revoked {revoked} subscription-sourced media_access rows for user {user_id}'
            )
        return

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f'Subscription with id {id} not found',
    )


@router.put(
    '/{id}',
    status_code=status.HTTP_201_CREATED,
    response_description='Update Subscription',
)
async def update_subscription(
    request: Request, id: int, update_info: dict, user_id: int = Depends(get_required_user_id)
):
    await get_entity_or_404(
        sub_repo.get_subscription_by_id,
        id,
        'Subscription',
        access_check=partial(
            sa_repo.check_subscription_owner_or_raise, user_id, is_admin=request.state.is_admin
        ),
    )

    try:
        updated_record = await sub_repo.update_subscription(id, update_info)
    except IntegrityError as e:
        # Switching audio_only moves the row onto the other media type's unique key.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='A subscription for this URL and media type already exists',
        ) from e
    if updated_record is not None:
        return updated_record
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f'Subscription with id {id} not found',
    )


@router.post(
    '/{id}/check',
    status_code=status.HTTP_202_ACCEPTED,
    response_description='Job id of the check, to poll at GET /tasks/{task}',
    response_model=dict,
)
async def check_subscription_now(
    request: Request, id: int, user_id: int = Depends(get_required_user_id)
):
    """Check one subscription for new videos now, instead of waiting for the schedule.

    Owner or admin only. Runs whether or not the subscription is enabled, and the videos it
    finds queue ahead of scheduled subscription downloads. Calling it again while a check of
    the same subscription is queued or running returns that check's id rather than starting
    another.
    """
    subscription = await get_entity_or_404(
        sub_repo.get_subscription_by_id,
        id,
        'Subscription',
        access_check=partial(
            sa_repo.check_subscription_owner_or_raise, user_id, is_admin=request.state.is_admin
        ),
    )
    task_id = await orch.submit(
        JobSpec(
            job_name=SUBSCRIPTION_CHECK_JOB,
            args=(id,),
            task_id=f'subscription-check-{id}',
            tracked=False,
            priority=0,
            # The owner, not the caller: everyone who presses it shares this task_id, and
            # GET /tasks/{id} lets only this user (or an admin) poll an untracked job.
            user_id=subscription.user_id,
        )
    )
    return {'task': task_id}


# --- Sharing endpoints ---


async def _grant_subscription(user_ids: list[int], subscription_id: int) -> None:
    # Grant subscription access + subscription-sourced media access in one transaction
    media_ids = await sub_repo.get_subscription_media_ids(subscription_id)
    await sharing_repo.share_subscription_with_users(user_ids, subscription_id, media_ids)
    if media_ids:
        logger.info(
            f'Granted subscription-sourced media_access for {len(media_ids)} media items '
            f'to users {user_ids} via subscription {subscription_id}'
        )


async def _revoke_subscription(target_user_id: int, subscription_id: int) -> bool:
    # Remove subscription access + subscription-sourced media access in one transaction
    removed, revoked = await sharing_repo.unshare_subscription_for_user(
        target_user_id, subscription_id
    )
    if revoked > 0:
        logger.info(
            f'Revoked {revoked} subscription-sourced media_access rows for user {target_user_id} '
            f'from subscription {subscription_id}'
        )
    return removed


register_sharing_routes(
    router,
    SharingConfig(
        entity_name='Subscription',
        noun='subscription',
        id_key='subscription_id',
        get_by_id=sub_repo.get_subscription_by_id,
        check_owner_or_raise=sa_repo.check_subscription_owner_or_raise,
        grant=_grant_subscription,
        revoke=_revoke_subscription,
        list_user_ids=sa_repo.get_users_with_access,
        bulk_note=(
            "Skips subscriptions the caller doesn't own (reported in errors) and preserves the\n"
            'per-subscription cascade that grants subscription-sourced media access.'
        ),
    ),
)
