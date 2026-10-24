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


def spread_vectors(count: int, seed: int, *, toward: float | None = None) -> list[str]:
    """`count` distinct unit vectors, optionally all leaning toward `vector(toward)`.

    HNSW folds identical vectors into one graph element, so a test that needs the
    index to rank many rows must give them distinct embeddings.
    """
    rng = np.random.default_rng(seed)
    vectors = rng.normal(size=(count, EMBEDDING_DIM))
    if toward is not None:
        vectors = vectors * 0.1 + toward * 10
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return ['[' + ','.join(f'{x:.6f}' for x in row) + ']' for row in vectors]


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
    media_type: MediaType = MediaType.VIDEO,
    spans: list[tuple[float, float]] | None = None,
    block_vectors: list[str] | None = None,
) -> int:
    """`spans` gives each block's (start, end); by default blocks are back-to-back 10 s.

    `block_vectors` gives each block its own embedding instead of `embedding_value`.
    """
    spans = spans or [(index * 10.0, index * 10.0 + 10) for index in range(len(blocks))]
    block_vectors = block_vectors or [vector(embedding_value)] * len(blocks)
    media = MediaDetails(
        url=url,
        media_type=media_type,
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

    for body, (start, end) in zip(blocks, spans, strict=True):
        block = TranscriptBlock(
            start_time=start,
            end_time=end,
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
    for block_id, embedding in zip(block_ids, block_vectors, strict=True):
        session.execute(
            text(
                'INSERT INTO transcript_embeddings (transcript_block_id, embedding) '
                'VALUES (:b, CAST(:e AS vector))'
            ),
            {'b': block_id, 'e': embedding},
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
