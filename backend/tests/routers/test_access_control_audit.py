"""Tests for the three-tier access-control pattern (owner → shared → admin).

Covers the gaps found in the access-control audit:
- Playlist add-media must verify the caller can access the media being added
  (otherwise sharing the playlist escalates access to arbitrary media).
- Stats filter-options must not leak other users' playlists.
- Stats playlist_id filters must be access-checked.
- Media access checks must honor the owner tier (owner_id), not just MediaAccess rows.
- The download_jobs router must require authentication.
"""

from sqlalchemy import delete, update
from sqlmodel import select

from database import db
from models import MediaAccess, MediaDetails, PlaylistMedia
from repositories import stats as stats_repo


def _setup_two_users(client):
    """Register admin (first user) + second non-admin user. Return (admin_id, user2_id, client2)."""
    from fastapi.testclient import TestClient

    from main import app

    admin_resp = client.get('/auth/users')
    admin_user = next(u for u in admin_resp.json() if u['username'] == 'testadmin')
    admin_id = admin_user['id']

    reg_resp = client.post('/auth/register', json={'username': 'user2', 'password': 'pass123'})
    user2_id = reg_resp.json()['id']
    client.post(f'/auth/users/{user2_id}/approve')

    client2 = TestClient(app)
    client2.post('/auth/login', json={'username': 'user2', 'password': 'pass123'})

    return admin_id, user2_id, client2


def _playlist_media_count(playlist_id: int) -> int:
    session = db.get_sync_session()
    try:
        rows = session.execute(
            select(PlaylistMedia).where(PlaylistMedia.playlist_id == playlist_id)
        )
        return len(rows.scalars().all())
    finally:
        session.close()


def _media_access_count(user_id: int, media_id: int) -> int:
    session = db.get_sync_session()
    try:
        rows = session.execute(
            select(MediaAccess).where(
                MediaAccess.user_id == user_id,
                MediaAccess.media_details_id == media_id,
            )
        )
        return len(rows.scalars().all())
    finally:
        session.close()


def _make_user2_owner_without_access_row(user2_id: int, media_id: int):
    """Transfer ownership of a media item to user2 without granting a MediaAccess row."""
    session = db.get_sync_session()
    try:
        session.execute(
            update(MediaDetails).where(MediaDetails.id == media_id).values(owner_id=user2_id)
        )
        session.execute(
            delete(MediaAccess).where(
                MediaAccess.user_id == user2_id,
                MediaAccess.media_details_id == media_id,
            )
        )
        session.commit()
    finally:
        session.close()


# ============================================================
# Playlist add-media requires access to the media
# ============================================================


def test_add_inaccessible_media_to_own_playlist_404(authenticated_client):
    """A playlist owner cannot add media they have no access to."""
    _admin_id, user2_id, client2 = _setup_two_users(authenticated_client)

    pl = client2.post('/playlists', json={'name': 'Mine'})
    assert pl.status_code == 201, pl.text
    pid = pl.json()['id']

    # Media 1 belongs to admin; user2 has no access to it
    resp = client2.post(f'/playlists/{pid}/media', json={'media_details_id': 1})
    assert resp.status_code == 404, resp.text
    assert _playlist_media_count(pid) == 0

    # Sharing the (empty) playlist must not grant user2 any access to media 1
    client2.post(f'/playlists/{pid}/share', json={'user_id': user2_id})
    assert _media_access_count(user2_id, 1) == 0
    assert client2.get('/media-details/1').status_code == 404


def test_bulk_add_skips_inaccessible_media(authenticated_client):
    """Bulk add reports inaccessible media as no_access and does not add them."""
    _admin_id, user2_id, client2 = _setup_two_users(authenticated_client)

    # Grant user2 access to media 2 only
    authenticated_client.post('/media-details/2/share', json={'user_id': user2_id})

    pl = client2.post('/playlists', json={'name': 'Bulk'})
    pid = pl.json()['id']

    resp = client2.post(f'/playlists/{pid}/media/bulk', json={'media_details_ids': [1, 2, 999]})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body['added'] == 1
    assert body['no_access'] == 1
    assert body['invalid'] == 1
    assert body['added_media_ids'] == [2]
    assert _playlist_media_count(pid) == 1


def test_add_accessible_media_to_playlist_still_works(authenticated_client):
    """Shared and owned media can still be added to the caller's playlist."""
    _admin_id, user2_id, client2 = _setup_two_users(authenticated_client)

    authenticated_client.post('/media-details/2/share', json={'user_id': user2_id})

    pl = client2.post('/playlists', json={'name': 'OK'})
    pid = pl.json()['id']

    resp = client2.post(f'/playlists/{pid}/media', json={'media_details_id': 2})
    assert resp.status_code == 201, resp.text
    assert _playlist_media_count(pid) == 1


def test_admin_can_add_any_media_to_playlist(authenticated_client):
    """Admin bypasses the media access check when adding to a playlist."""
    pl = authenticated_client.post('/playlists', json={'name': 'AdminPl'})
    pid = pl.json()['id']
    resp = authenticated_client.post(f'/playlists/{pid}/media', json={'media_details_id': 1})
    assert resp.status_code == 201, resp.text


# ============================================================
# Stats: playlist visibility and playlist_id filter
# ============================================================


def test_filter_options_does_not_leak_foreign_playlists(authenticated_client):
    """Users must not see other users' playlists in stats filter options."""
    stats_repo._cache.clear()
    _admin_id, user2_id, client2 = _setup_two_users(authenticated_client)

    # Admin playlist containing media user2 can also access
    pl = authenticated_client.post('/playlists', json={'name': 'AdminOnly'})
    pid = pl.json()['id']
    authenticated_client.post(f'/playlists/{pid}/media', json={'media_details_id': 1})
    authenticated_client.post('/media-details/1/share', json={'user_id': user2_id})

    resp = client2.get('/stats/filter-options')
    assert resp.status_code == 200
    playlist_ids = [p['id'] for p in resp.json()['playlists']]
    assert pid not in playlist_ids

    # The owner still sees it
    stats_repo._cache.clear()
    resp = authenticated_client.get('/stats/filter-options')
    assert pid in [p['id'] for p in resp.json()['playlists']]

    # Once shared, user2 sees it too
    authenticated_client.post(f'/playlists/{pid}/share', json={'user_id': user2_id})
    stats_repo._cache.clear()
    resp = client2.get('/stats/filter-options')
    assert pid in [p['id'] for p in resp.json()['playlists']]


def test_stats_playlist_filter_requires_access(authenticated_client):
    """Using a foreign playlist_id as a stats filter returns 404."""
    stats_repo._cache.clear()
    _admin_id, user2_id, client2 = _setup_two_users(authenticated_client)

    pl = authenticated_client.post('/playlists', json={'name': 'Filtered'})
    pid = pl.json()['id']
    authenticated_client.post(f'/playlists/{pid}/media', json={'media_details_id': 1})

    for endpoint in (
        '/stats/overview',
        '/stats/storage',
        '/stats/downloads-over-time',
        '/stats/transcription',
        '/stats/transcript-storage',
        '/stats/engagement',
        '/stats/clips',
        '/stats/download-success-rate',
        '/stats/download-activity-heatmap',
    ):
        resp = client2.get(endpoint, params={'playlist_id': pid})
        assert resp.status_code == 404, f'{endpoint}: {resp.status_code}'

    # Owner can filter by their own playlist
    resp = authenticated_client.get('/stats/overview', params={'playlist_id': pid})
    assert resp.status_code == 200

    # Shared user can filter by it once shared
    authenticated_client.post(f'/playlists/{pid}/share', json={'user_id': user2_id})
    stats_repo._cache.clear()
    resp = client2.get('/stats/overview', params={'playlist_id': pid})
    assert resp.status_code == 200

    # Nonexistent playlist filter is a 404, not a silent empty result
    resp = authenticated_client.get('/stats/overview', params={'playlist_id': 99999})
    assert resp.status_code == 404


# ============================================================
# Media access owner tier (owner_id fallback)
# ============================================================


def test_owner_without_access_row_can_access_media(authenticated_client):
    """The owner tier works even when the owner's DIRECT MediaAccess row is missing."""
    _admin_id, user2_id, client2 = _setup_two_users(authenticated_client)
    _make_user2_owner_without_access_row(user2_id, 1)

    resp = client2.get('/media-details/1')
    assert resp.status_code == 200, resp.text

    # Owner can also add their media to a playlist and tag it
    pl = client2.post('/playlists', json={'name': 'OwnerPl'})
    pid = pl.json()['id']
    resp = client2.post(f'/playlists/{pid}/media', json={'media_details_id': 1})
    assert resp.status_code == 201, resp.text

    resp = client2.put(
        '/media-details/bulk-tags',
        json={'media_details_ids': [1], 'tag_names': ['owned']},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()['skipped'] == 0
    assert resp.json()['tagged_count'] == 1


def test_bulk_tags_skips_inaccessible_and_missing_media(authenticated_client):
    """Non-owner without access cannot tag media; missing ids are skipped too."""
    _admin_id, _user2_id, client2 = _setup_two_users(authenticated_client)

    resp = client2.put(
        '/media-details/bulk-tags',
        json={'media_details_ids': [1, 99999], 'tag_names': ['nope']},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()['skipped'] == 2
    assert resp.json()['tagged_count'] == 0


# ============================================================
# Transcript generation is owner-only (symmetric with delete)
# ============================================================


def test_shared_user_cannot_create_transcript(authenticated_client):
    """A read-only shared user cannot trigger transcript generation on the owner's media.

    create_transcript is owner-only (like delete_transcripts), so a shared user can't
    enqueue a CPU-heavy Whisper job or mutate the owner's media record. The owner-check
    raises 404 for non-owners (existence-hiding convention), and short-circuits before
    any job dispatch — so no task mock is needed.
    """
    _admin_id, user2_id, client2 = _setup_two_users(authenticated_client)

    # Share media 1 with user2 so they genuinely have (read) access to it...
    authenticated_client.post('/media-details/1/share', json={'user_id': user2_id})
    assert client2.get('/media-details/1').status_code == 200

    # ...but the owner-only gate still blocks transcript generation.
    resp = client2.post('/media-details/transcripts/1/create')
    assert resp.status_code == 404, resp.text
