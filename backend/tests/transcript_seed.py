"""Seeding helpers for tests that search transcripts through real pgvector SQL."""

from datetime import datetime

import numpy as np
from sqlalchemy import text

from models import (
    MediaAccess,
    MediaDetails,
    MediaType,
    SourceType,
    TaskStatus,
    TranscriptBlock,
    User,
)

EMBEDDING_DIM = 384


class StubEmbedder:
    """Stands in for OnnxEmbedder so no ONNX session is loaded for a SQL test."""

    def encode(self, sentences, normalize_embeddings=True):
        return np.full((len(sentences), EMBEDDING_DIM), 0.05, dtype=np.float32)


def vector(value: float) -> str:
    return '[' + ','.join([str(value)] * EMBEDDING_DIM) + ']'


def seed_user(session, username: str) -> int:
    user = User(username=username, password_hash='x', is_approved=True)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user.id


def seed_media(
    session,
    *,
    owner_id: int,
    url: str,
    channel: str,
    title: str,
    released: datetime,
    blocks: list[str],
    status: str = TaskStatus.COMPLETE.value,
    grant_access_to: int | None = None,
    embedding_value: float = 0.05,
) -> int:
    media = MediaDetails(
        url=url,
        media_type=MediaType.VIDEO,
        channel=channel,
        title=title,
        status=status,
        release_timestamp=released,
        downloaded_at=released,
        owner_id=owner_id,
    )
    session.add(media)
    session.commit()
    session.refresh(media)

    for index, body in enumerate(blocks):
        block = TranscriptBlock(
            start_time=float(index * 10),
            end_time=float(index * 10 + 10),
            text=body,
            media_details_id=media.id,
        )
        session.add(block)
    session.commit()

    block_ids = [
        row[0]
        for row in session.execute(
            text('SELECT id FROM transcript_blocks WHERE media_details_id = :m ORDER BY id'),
            {'m': media.id},
        )
    ]
    for block_id in block_ids:
        session.execute(
            text(
                'INSERT INTO transcript_embeddings (transcript_block_id, embedding) '
                'VALUES (:b, CAST(:e AS vector))'
            ),
            {'b': block_id, 'e': vector(embedding_value)},
        )

    if grant_access_to is not None:
        session.add(
            MediaAccess(
                user_id=grant_access_to,
                media_details_id=media.id,
                source_type=SourceType.DIRECT,
                source_id=0,
            )
        )
    session.commit()
    return media.id
