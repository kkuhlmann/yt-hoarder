"""What the browser extension needs beyond the two add-only routes it shares with the UI."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dependencies import get_extension_poll_token, get_extension_token
from models import MediaType, TaskRecord, TaskStatus, TaskType
from repositories import task_records as tr_repo
from repositories.access_tokens import ResolvedToken
from routers.ytdl_router import get_app_version

router = APIRouter()

TASK_FEED_LIMIT = 25
TASK_FEED_FINISHED_SLOTS = 10
TASK_FEED_FINISHED_WITHIN = timedelta(hours=1)


class ExtensionIdentity(BaseModel):
    username: str
    token_name: str
    token_prefix: str
    expires_at: str | None
    app_version: str


@router.get('/me', response_model=ExtensionIdentity)
async def extension_identity(resolved: ResolvedToken = Depends(get_extension_token)):
    """Who an extension token acts as. The extension's connection test.

    Authenticates by `Authorization: Bearer` with an extension token only; the session
    cookie is never accepted. A server without this route predates extension tokens.
    """
    token = resolved.token
    return ExtensionIdentity(
        username=resolved.user.username,
        token_name=token.name,
        token_prefix=token.token_prefix,
        expires_at=token.expires_at.isoformat() if token.expires_at else None,
        app_version=get_app_version(),
    )


class ExtensionTask(BaseModel):
    """The slice of a task record the popup shows. Not the payload or the owner."""

    task_id: str
    task_type: TaskType
    status: TaskStatus
    title: str | None
    channel: str | None
    media_type: MediaType | None
    percent_complete: int
    eta_seconds: float | None
    status_message: str | None
    download_phase: str | None
    retry_count: int
    next_retry_at: datetime | None
    sleep_until: datetime | None
    error_code: str | None
    updated_at: datetime


class ExtensionTaskList(BaseModel):
    tasks: list[ExtensionTask]
    has_more: bool


def _extension_task(record: TaskRecord) -> ExtensionTask:
    data = {name: getattr(record, name) for name in ExtensionTask.model_fields}
    # Stored as naive UTC. Say so on the wire rather than leave a client to guess the zone.
    for name in ('next_retry_at', 'sleep_until', 'updated_at'):
        if data[name] is not None:
            data[name] = data[name].replace(tzinfo=UTC)
    return ExtensionTask(**data)


@router.get('/tasks', response_model=ExtensionTaskList)
async def extension_tasks(resolved: ResolvedToken = Depends(get_extension_poll_token)):
    """The token user's running and queued tasks, and those that finished in the last hour.

    Authenticates by `Authorization: Bearer` with an extension token only. Always the
    token user's own tasks, never an admin view. Polling is charged to its own rate budget,
    so refreshing a list can't use up the allowance for starting downloads.
    """
    records, has_more = await tr_repo.get_progress_feed(
        resolved.user.id,
        finished_within=TASK_FEED_FINISHED_WITHIN,
        limit=TASK_FEED_LIMIT,
        finished_slots=TASK_FEED_FINISHED_SLOTS,
    )
    return ExtensionTaskList(tasks=[_extension_task(r) for r in records], has_more=has_more)
