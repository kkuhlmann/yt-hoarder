"""How raw transcript segments are cut into the ~20 s blocks that search and MCP read."""

from itertools import pairwise

from services.transcript import _aggregate_segments_into_blocks


def _blocks(segments):
    return _aggregate_segments_into_blocks(segments, 1, block_duration=20, transcript_model='t')


def _spans(blocks):
    return [(block.start_time, block.end_time) for block in blocks]


def test_blocks_partition_overlapping_caption_cues():
    # YouTube auto-captions roll: each cue is still on screen when the next starts.
    cues = [(2.0 * i, 2.0 * i + 4.5, f'cue {i}') for i in range(40)]

    blocks = _blocks(cues)

    assert len(blocks) > 1
    for start, end in _spans(blocks):
        assert start < end
    for (_, end), (next_start, _) in pairwise(_spans(blocks)):
        assert end == next_start
    assert ' '.join(b.text for b in blocks) == ' '.join(text for _, _, text in cues)


def test_a_silence_gap_keeps_the_block_on_its_own_last_cue():
    cues = [(0.0, 5.0, 'a'), (5.0, 10.0, 'b'), (10.0, 15.0, 'c'), (15.0, 17.4, 'd')]
    cues.append((40.0, 45.0, 'e'))

    assert _spans(_blocks(cues)) == [(0, 18), (40, 45)]


def test_a_block_ends_at_the_latest_end_of_its_own_cues():
    cues = [(0.0, 5.0, 'a'), (3.0, 12.2, 'b'), (8.0, 10.0, 'c')]

    assert _spans(_blocks(cues)) == [(0, 13)]


def test_no_block_is_zero_length():
    assert _spans(_blocks([(5.0, 5.0, 'blip')])) == [(5, 6)]
