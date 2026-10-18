"""Personal access tokens for the MCP endpoint, managed with the normal cookie session."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from config import settings
from dependencies import get_admin_user_id, get_required_user_id
from logger import logger
from models import PersonalAccessToken, User, utc_now
from repositories import access_tokens as token_repo
from repositories import users as user_repo
from repositories.access_tokens import MAX_TOKENS_PER_USER, TokenStatus

router = APIRouter()


class TokenCreateRequest(BaseModel):
    name: str = Field(max_length=64)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator('name')
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            msg = 'Name is required'
            raise ValueError(msg)
        return value


class TokenResponse(BaseModel):
    id: int
    name: str
    token_prefix: str
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    status: TokenStatus


class TokenCreatedResponse(TokenResponse):
    token: str = Field(description='The token itself. Returned once, never stored.')


class RevokeAllResponse(BaseModel):
    revoked: int


def _token_response(token: PersonalAccessToken, user: User) -> TokenResponse:
    return TokenResponse(
        id=token.id,
        name=token.name,
        token_prefix=token.token_prefix,
        created_at=token.created_at.isoformat(),
        last_used_at=token.last_used_at.isoformat() if token.last_used_at else None,
        expires_at=token.expires_at.isoformat() if token.expires_at else None,
        status=token_repo.token_status(token, user, utc_now()),
    )


@router.get('', response_model=list[TokenResponse])
async def list_tokens(user_id: int = Depends(get_required_user_id)):
    """List your MCP access tokens, newest first, with why any of them no longer work."""
    user = await user_repo.get_user_by_id(user_id)
    tokens = await token_repo.list_tokens_for_user(user_id)
    return [_token_response(token, user) for token in tokens]


@router.post('', status_code=status.HTTP_201_CREATED, response_model=TokenCreatedResponse)
async def create_token(body: TokenCreateRequest, user_id: int = Depends(get_required_user_id)):
    """Create an MCP access token. The response carries the token itself, exactly once."""
    if not settings.mcp.enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='The MCP server is not enabled'
        )
    if await token_repo.count_tokens_for_user(user_id) >= MAX_TOKENS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'You already have {MAX_TOKENS_PER_USER} tokens. Revoke one first.',
        )

    user = await user_repo.get_user_by_id(user_id)
    token, raw = await token_repo.create_token(user_id, body.name, body.expires_in_days)
    logger.info(f'User {user_id} created MCP token id={token.id} ({token.token_prefix}…)')
    return TokenCreatedResponse(**_token_response(token, user).model_dump(), token=raw)


@router.delete('/{token_id}', status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(token_id: int, user_id: int = Depends(get_required_user_id)):
    """Revoke one of your tokens. Agents using it get 401 from their next call."""
    if not await token_repo.delete_token(user_id, token_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Token not found')
    logger.info(f'User {user_id} revoked MCP token id={token_id}')


@router.delete('/users/{user_id}', response_model=RevokeAllResponse)
async def revoke_all_tokens_for_user(user_id: int, admin_id: int = Depends(get_admin_user_id)):
    """Revoke every token a user holds. Admin only.

    Works while MCP is disabled too, so an admin can clean up before turning it off.
    """
    revoked = await token_repo.delete_all_tokens_for_user(user_id)
    logger.info(f'Admin {admin_id} revoked {revoked} MCP token(s) for user id={user_id}')
    return RevokeAllResponse(revoked=revoked)
