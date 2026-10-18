"""Authentication router: register, login, logout, me, setup-status, admin user management."""

import glob
import mimetypes
import os
import tempfile
from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from auth import (
    create_jwt_token,
    generate_recovery_code,
    generate_temp_password,
    hash_password,
    verify_password,
)
from config import settings
from database import db
from dependencies import get_admin_user_id, get_required_user_id
from logger import logger
from models import BACKGROUNDS_DIR, MIN_PASSWORD_LENGTH, MediaAccess, User, utc_now
from rate_limit import login_rate_limit, recovery_rate_limit, register_rate_limit
from repositories import access_tokens as token_repo
from repositories import users as user_repo
from services import admin_recovery

router = APIRouter()

COOKIE_MAX_AGE = settings.auth.jwt_expiry_days * 24 * 60 * 60  # days -> seconds
RECOVERY_CODE_TTL = timedelta(minutes=15)


class AuthRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    is_admin: bool
    is_approved: bool
    must_change_password: bool = False
    geo_background_preset: str | None = None
    has_geo_background: bool = False
    # Instance-wide rather than per-user, but carried here because every client
    # already fetches /me on boot and uses it to decide what to show.
    mcp_enabled: bool = False


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        is_approved=user.is_approved,
        must_change_password=user.must_change_password,
        geo_background_preset=user.geo_background_preset,
        has_geo_background=bool(user.geo_background_filename),
        mcp_enabled=settings.mcp.enabled,
    )


def _found_user(user):
    """404 when a user row is missing; the detail text is observable API."""
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    return user


def _validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Password must be at least {MIN_PASSWORD_LENGTH} characters',
        )


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key='auth_token',
        value=token,
        httponly=True,
        samesite='lax',
        secure=settings.auth.cookie_secure,
        max_age=COOKIE_MAX_AGE,
        path='/',
    )


@router.get('/setup-status')
async def setup_status():
    """Check if the app needs initial setup (no users exist)."""
    count = await user_repo.get_user_count()
    return {'needs_setup': count == 0}


@router.post(
    '/register',
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(register_rate_limit)],
)
async def register(body: AuthRequest, response: Response):
    """Register a new user.

    First user is auto-admin + auto-approved + auto-logged-in.
    Subsequent users are pending approval.
    """
    if len(body.username) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Username must be at least 3 characters',
        )
    _validate_password(body.password)

    existing = await user_repo.get_user_by_username(body.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Username already taken',
        )

    user_count = await user_repo.get_user_count()
    is_first_user = user_count == 0

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        is_admin=is_first_user,
        is_approved=is_first_user,
    )
    user = await user_repo.create_user(user)

    result = _user_response(user)

    if is_first_user:
        token = create_jwt_token(user.id, user.username, user.is_admin)
        _set_auth_cookie(response, token)

    return result


@router.post('/login', dependencies=[Depends(login_rate_limit)])
async def login(body: AuthRequest, response: Response):
    """Authenticate a user and set JWT cookie."""
    user = await user_repo.get_user_by_username(body.username)
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid username or password',
        )

    token = create_jwt_token(user.id, user.username, user.is_admin)
    _set_auth_cookie(response, token)

    return _user_response(user)


@router.post('/logout')
async def logout(response: Response):
    """Clear the auth cookie."""
    response.delete_cookie(key='auth_token', path='/')
    return {'status': 'ok'}


@router.get('/me')
async def me(request: Request):
    """Get the current authenticated user's info.

    is_approved is always fetched from DB (not from the JWT) so that
    revoking approval takes effect immediately.
    """
    user_id = request.state.user_id
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Not authenticated',
        )

    user = await user_repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='User not found',
        )

    return _user_response(user)


# --- Password recovery ---


class UsernameRequest(BaseModel):
    username: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AdminRecoveryCompleteRequest(BaseModel):
    username: str
    code: str
    new_password: str


@router.post('/forgot-password', dependencies=[Depends(recovery_rate_limit)])
async def forgot_password(body: UsernameRequest):
    """Flag an account as needing an admin-issued password reset.

    Always succeeds, whether or not the account exists — the response must not reveal
    which usernames are registered. Repeat requests keep the original timestamp so the
    admin sees when the user first asked and the flag cannot be used to spam them.
    """
    user = await user_repo.get_user_by_username(body.username)
    if user and user.password_reset_requested_at is None:
        await user_repo.update_user(user.id, password_reset_requested_at=utc_now())
        logger.info(f'User {user.username} (id={user.id}) requested a password reset')

    return {'status': 'ok'}


@router.post('/admin-recovery/request', dependencies=[Depends(recovery_rate_limit)])
async def request_admin_recovery(body: UsernameRequest):
    """Write a single-use recovery code to a file on the server's filesystem.

    Recovering an admin account requires access to the machine yt-hoarder runs on:
    the code is only ever written to disk, never returned here or logged. The response
    is identical for unknown and non-admin usernames so it cannot be used to discover
    which accounts are admins.

    A request while an unexpired code is outstanding is a no-op, so repeated calls
    can't churn the file or invalidate a code the admin is already fetching.
    """
    user = await user_repo.get_user_by_username(body.username)
    now = utc_now()
    should_issue = (
        user is not None
        and user.is_admin
        and (user.recovery_code_expires_at is None or user.recovery_code_expires_at <= now)
    )

    if should_issue:
        code = generate_recovery_code()
        expires_at = now + RECOVERY_CODE_TTL
        try:
            admin_recovery.write_recovery_file(user.username, code, expires_at)
        except PermissionError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f'Permission denied writing to {admin_recovery.RECOVERY_FILE_PATH}. '
                'On Linux, run: sudo chown 1000:1000 data/',
            ) from e
        await user_repo.update_user(
            user.id,
            recovery_code_hash=hash_password(code),
            recovery_code_expires_at=expires_at,
        )

    return {
        'status': 'ok',
        'file_path': admin_recovery.RECOVERY_FILE_PATH,
        'expires_in_minutes': int(RECOVERY_CODE_TTL.total_seconds() // 60),
    }


@router.post('/admin-recovery/complete', dependencies=[Depends(recovery_rate_limit)])
async def complete_admin_recovery(body: AdminRecoveryCompleteRequest, response: Response):
    """Set a new admin password using the code from the recovery file, and sign in.

    Every rejection returns the same message: a caller guessing codes learns nothing
    about whether the username, the code, or the expiry was the problem.
    """
    _validate_password(body.new_password)

    invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail='Invalid or expired recovery code',
    )

    user = await user_repo.get_user_by_username(body.username)
    if not user or not user.is_admin or not user.recovery_code_hash:
        raise invalid
    if user.recovery_code_expires_at is None or user.recovery_code_expires_at <= utc_now():
        raise invalid
    if not verify_password(body.code, user.recovery_code_hash):
        raise invalid

    user = await user_repo.update_user(
        user.id,
        password_hash=hash_password(body.new_password),
        password_changed_at=utc_now(),
        must_change_password=False,
        password_reset_requested_at=None,
        recovery_code_hash=None,
        recovery_code_expires_at=None,
    )
    admin_recovery.delete_recovery_file()
    logger.warning(f'Admin {user.username} (id={user.id}) recovered their account via file code')

    _set_auth_cookie(response, create_jwt_token(user.id, user.username, user.is_admin))
    return _user_response(user)


@router.post('/me/change-password')
async def change_password(body: ChangePasswordRequest, request: Request, response: Response):
    """Change the signed-in user's own password.

    Signs out every other session by invalidating tokens issued before now, and
    re-issues this caller's cookie so the request they are making doesn't log them out.

    Auth is checked inline rather than via get_required_user_id: a user holding a
    temporary password is blocked by that dependency, and this is the endpoint they
    need in order to clear it.
    """
    user_id = request.state.user_id
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Not authenticated',
        )

    _validate_password(body.new_password)

    user = await user_repo.get_user_by_id(user_id)
    if not user or not verify_password(body.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Current password is incorrect',
        )

    user = await user_repo.update_user(
        user_id,
        password_hash=hash_password(body.new_password),
        password_changed_at=utc_now(),
        must_change_password=False,
        password_reset_requested_at=None,
    )
    logger.info(f'User {user.username} (id={user_id}) changed their password')

    _set_auth_cookie(response, create_jwt_token(user.id, user.username, user.is_admin))
    return _user_response(user)


# --- Admin endpoints ---


class UserListResponse(BaseModel):
    id: int
    username: str
    is_admin: bool
    is_approved: bool
    created_at: str
    media_count: int
    storage_limit_bytes: int | None
    storage_used_bytes: int
    password_reset_requested_at: str | None = None
    must_change_password: bool = False
    token_count: int = 0


class StorageLimitRequest(BaseModel):
    storage_limit_bytes: int | None = None


class StorageResponse(BaseModel):
    storage_used_bytes: int
    storage_limit_bytes: int | None


@router.get('/users/shareable')
async def list_shareable_users(_user_id: int = Depends(get_required_user_id)):
    """List all approved users (id + username only). Any authenticated user can call."""
    users = await user_repo.get_all_approved_users()
    return [{'id': u.id, 'username': u.username} for u in users]


@router.get('/users')
async def list_users(_admin_id: int = Depends(get_admin_user_id)):
    """List all users with their media counts and storage usage. Admin only."""
    from sqlalchemy import func, select

    async with db.get_async_session() as session:
        stmt = (
            select(
                User,
                func.count(MediaAccess.id).label('media_count'),
            )
            .outerjoin(MediaAccess, MediaAccess.user_id == User.id)
            .group_by(User.id)
            .order_by(User.created_at)
        )
        result = await session.execute(stmt)
        rows = result.all()

    token_counts = await token_repo.count_tokens_by_user()
    response = []
    for user, media_count in rows:
        storage_used = await user_repo.get_user_storage_usage(user.id)
        response.append(
            UserListResponse(
                id=user.id,
                username=user.username,
                is_admin=user.is_admin,
                is_approved=user.is_approved,
                created_at=user.created_at.isoformat() if user.created_at else '',
                media_count=media_count,
                storage_limit_bytes=user.storage_limit_bytes,
                storage_used_bytes=storage_used,
                password_reset_requested_at=(
                    user.password_reset_requested_at.isoformat()
                    if user.password_reset_requested_at
                    else None
                ),
                must_change_password=user.must_change_password,
                token_count=token_counts.get(user.id, 0),
            )
        )

    return response


@router.post('/users/{user_id}/approve')
async def approve_user(user_id: int, admin_id: int = Depends(get_admin_user_id)):
    """Approve a pending user. Admin only."""
    user = _found_user(await user_repo.update_user(user_id, is_approved=True))
    logger.info(f'Admin {admin_id} approved user {user.username} (id={user_id})')
    return _user_response(user)


@router.post('/users/{user_id}/reset-password')
async def reset_user_password(user_id: int, admin_id: int = Depends(get_admin_user_id)):
    """Issue a temporary password for a user. Admin only.

    The generated password is returned once, in this response only — there is no email
    integration, so the admin relays it to the user out-of-band. It is strictly temporary:
    the user must choose their own password before they can use the app, so the admin never
    holds a lasting credential for someone else's account.

    Resetting your own account this way is rejected: it would invalidate the cookie you
    are making the request with. Use /auth/me/change-password instead.
    """
    if user_id == admin_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Use Change Password to change your own password',
        )

    temp_password = generate_temp_password()
    user = _found_user(
        await user_repo.update_user(
            user_id,
            password_hash=hash_password(temp_password),
            password_changed_at=utc_now(),
            must_change_password=True,
            password_reset_requested_at=None,
        )
    )

    logger.info(f'Admin {admin_id} reset the password for user {user.username} (id={user_id})')
    return {'temporary_password': temp_password}


@router.delete('/users/{user_id}/reset-request', status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_reset_request(user_id: int, admin_id: int = Depends(get_admin_user_id)):
    """Clear a user's password reset request without resetting their password. Admin only."""
    _found_user(await user_repo.update_user(user_id, password_reset_requested_at=None))
    logger.info(f'Admin {admin_id} dismissed the reset request for user id={user_id}')


@router.delete('/users/{user_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, admin_id: int = Depends(get_admin_user_id)):
    """Delete a user. Admin only. Cannot delete yourself."""
    if user_id == admin_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Cannot delete your own account',
        )
    user = await user_repo.get_user_by_id(user_id)
    if user and user.geo_background_filename:
        bg_path = os.path.join(BACKGROUNDS_DIR, user.geo_background_filename)
        if os.path.isfile(bg_path):
            os.unlink(bg_path)

    _found_user(await user_repo.delete_user(user_id))
    logger.info(f'Admin {admin_id} deleted user id={user_id}')


@router.get('/me/storage')
async def my_storage(user_id: int = Depends(get_required_user_id)):
    """Get the current user's storage usage and limit."""
    user = await user_repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User not found')

    storage_used = await user_repo.get_user_storage_usage(user_id)
    return StorageResponse(
        storage_used_bytes=storage_used,
        storage_limit_bytes=user.storage_limit_bytes,
    )


# --- Geo background endpoints ---

MAX_GEO_BACKGROUND_SIZE = 200 * 1024  # 200KB
ALLOWED_IMAGE_TYPES = {
    'image/png': '.png',
    'image/gif': '.gif',
    'image/jpeg': '.jpg',
    'image/webp': '.webp',
}
VALID_PRESETS = {'starfield', 'stripes', 'checkerboard', 'custom'}


class GeoBackgroundPresetRequest(BaseModel):
    preset: str | None = None


@router.put('/me/geo-background-preset')
async def set_geo_background_preset(
    body: GeoBackgroundPresetRequest,
    user_id: int = Depends(get_required_user_id),
):
    """Set the GeoCities background preset. None = default polka dots."""
    if body.preset is not None and body.preset not in VALID_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Invalid preset. Choose from: {", ".join(sorted(VALID_PRESETS))} or null for default',
        )

    user = _found_user(await user_repo.update_user(user_id, geo_background_preset=body.preset))

    return {'preset': user.geo_background_preset}


@router.post('/me/geo-background')
async def upload_geo_background(
    file: UploadFile = File(...),
    user_id: int = Depends(get_required_user_id),
):
    """Upload a custom tiling background image for the GeoCities theme."""
    content = await file.read()

    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='File is empty')

    if len(content) > MAX_GEO_BACKGROUND_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Image too large (max {MAX_GEO_BACKGROUND_SIZE // 1024}KB)',
        )

    content_type = file.content_type or ''
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Unsupported image type. Allowed: {", ".join(sorted(ALLOWED_IMAGE_TYPES))}',
        )

    ext = ALLOWED_IMAGE_TYPES[content_type]
    filename = f'{user_id}{ext}'
    os.makedirs(BACKGROUNDS_DIR, exist_ok=True)

    # Remove any previous background file with a different extension
    for old_file in glob.glob(os.path.join(BACKGROUNDS_DIR, f'{user_id}.*')):
        os.unlink(old_file)

    # Atomic write: temp file + rename
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.tmp', dir=BACKGROUNDS_DIR)
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
        os.replace(tmp_path, os.path.join(BACKGROUNDS_DIR, filename))
    except PermissionError as e:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f'Permission denied writing to {BACKGROUNDS_DIR}. '
            'On Linux, run: sudo chown 1000:1000 data/',
        ) from e
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    await user_repo.update_user(
        user_id, geo_background_filename=filename, geo_background_preset='custom'
    )
    logger.info(f'User {user_id} uploaded geo background ({len(content)} bytes)')

    return {'status': 'uploaded', 'filename': filename}


@router.get('/me/geo-background')
async def get_geo_background(user_id: int = Depends(get_required_user_id)):
    """Serve the user's custom GeoCities background image."""
    user = await user_repo.get_user_by_id(user_id)
    if not user or not user.geo_background_filename:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='No custom background set'
        )

    file_path = os.path.join(BACKGROUNDS_DIR, user.geo_background_filename)
    if not os.path.isfile(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Background file not found'
        )

    media_type = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={'Cache-Control': 'public, max-age=86400'},
    )


@router.delete('/me/geo-background')
async def delete_geo_background(user_id: int = Depends(get_required_user_id)):
    """Remove the user's custom background image and reset to default."""
    user = await user_repo.get_user_by_id(user_id)
    if user and user.geo_background_filename:
        file_path = os.path.join(BACKGROUNDS_DIR, user.geo_background_filename)
        if os.path.isfile(file_path):
            os.unlink(file_path)

    await user_repo.update_user(user_id, geo_background_filename=None, geo_background_preset=None)
    return {'status': 'deleted'}


@router.put('/users/{user_id}/storage-limit')
async def set_storage_limit(
    user_id: int,
    body: StorageLimitRequest,
    admin_id: int = Depends(get_admin_user_id),
):
    """Set or clear a user's storage limit. Admin only. NULL = unlimited."""
    if body.storage_limit_bytes is not None and body.storage_limit_bytes < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Storage limit must be non-negative',
        )

    user = _found_user(
        await user_repo.update_user(user_id, storage_limit_bytes=body.storage_limit_bytes)
    )

    logger.info(
        f'Admin {admin_id} set storage limit for user {user_id} to {body.storage_limit_bytes}'
    )
    return {'storage_limit_bytes': user.storage_limit_bytes}
