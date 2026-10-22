"""Execution lanes: named queues with a configurable width and priority ordering.

- default       (orchestration work; defaults to 2)
- subscriptions (channel/playlist enumeration; defaults to 1)
- downloads     (downloads, clips fetched from YouTube; defaults to 1)
- ml            (transcription/clips/sprites; defaults to 1)

Widths come from the app_settings row and are editable live in the Settings UI
(see set_concurrency).

Subscription pipelines get their own lane because one holds its slot for an
entire channel enumeration plus a per-video DB check, and priority cannot
preempt a *running* job — on the default lane the two cron job types could
occupy both slots and stall manual downloads for minutes.

Entries are popped in (priority ASC, queue_sequence ASC) order — the same
ordering the UI reconstructs from TaskRecord. Equal keys dequeue in submission
order: pop_next uses min(), which returns the first minimal element of an
append-ordered list. Untracked jobs all tie at queue_sequence None (+inf), so
that tie-break is what orders them.

Thread-safety model: all queue mutations happen on the event-loop thread
(submissions from worker threads are marshalled via call_soon_threadsafe),
so plain lists need no locks. Dispatchers wait on an asyncio.Event.
"""

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any

from .jobs import JobSpec


@dataclass
class JobHandle:
    task_id: str
    spec: JobSpec
    lane: str
    state: str = 'QUEUED'  # QUEUED | RUNNING
    cancel_event: Any = None  # threading.Event, set lazily by core
    child_process: Any = None  # multiprocessing.Process for subprocess jobs
    # True when the cancel came from orchestrator shutdown rather than a user:
    # the wrapper then leaves DB state untouched for startup recovery.
    shutdown: bool = False


@dataclass
class QueueEntry:
    spec: JobSpec
    handle: JobHandle

    def sort_key(self) -> tuple:
        seq = self.spec.queue_sequence
        return (
            self.spec.priority if self.spec.priority is not None else 5,
            seq if seq is not None else math.inf,
        )


@dataclass
class Lane:
    name: str
    concurrency: int
    entries: list[QueueEntry] = field(default_factory=list)
    running: dict[str, QueueEntry] = field(default_factory=dict)
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    slots: asyncio.Semaphore = None  # created in __post_init__
    # Permits actually in circulation, which trails `concurrency` after a shrink.
    capacity: int = field(default=0, init=False)

    def __post_init__(self):
        self.slots = asyncio.Semaphore(self.concurrency)
        self.capacity = self.concurrency

    def set_concurrency(self, value: int) -> None:
        """Retarget the lane's width. Never touches a running job.

        Growth is immediate: releasing permits unblocks a dispatcher already
        waiting on one. A shrink can only be lazy — the surplus permits are held
        by running jobs, and taking one back means killing its job — so it lands
        via absorb_surplus_permit as those jobs finish.
        """
        self.concurrency = max(1, value)
        while self.capacity < self.concurrency:
            self.capacity += 1
            self.slots.release()

    def absorb_surplus_permit(self) -> bool:
        """Retire the permit the dispatcher just acquired if the lane has shrunk.

        When this returns True the caller must neither dispatch nor release: the
        permit is gone on purpose, and releasing it would undo the shrink.
        """
        if self.capacity > self.concurrency:
            self.capacity -= 1
            return True
        return False

    def add(self, entry: QueueEntry) -> None:
        self.entries.append(entry)
        self.wakeup.set()

    def pop_next(self) -> QueueEntry | None:
        """Remove and return the entry with the lowest (priority, queue_sequence)."""
        if not self.entries:
            return None
        entry = min(self.entries, key=QueueEntry.sort_key)
        self.entries.remove(entry)
        return entry

    def remove(self, task_id: str) -> QueueEntry | None:
        for entry in self.entries:
            if entry.handle.task_id == task_id:
                self.entries.remove(entry)
                return entry
        return None

    def reprioritize(self, task_id: str, priority: int = 0, queue_sequence: int = 0) -> bool:
        """Move a queued entry to the front of the lane. Returns False if not queued."""
        for entry in self.entries:
            if entry.handle.task_id == task_id:
                entry.spec.priority = priority
                entry.spec.queue_sequence = queue_sequence
                return True
        return False

    def snapshot(self) -> dict:
        return {
            'concurrency': self.concurrency,
            'capacity': self.capacity,
            'queued': [
                {
                    'task_id': e.handle.task_id,
                    'job_name': e.spec.job_name,
                    'priority': e.spec.priority,
                    'queue_sequence': e.spec.queue_sequence,
                }
                for e in sorted(self.entries, key=QueueEntry.sort_key)
            ],
            'running': sorted(self.running),
        }
