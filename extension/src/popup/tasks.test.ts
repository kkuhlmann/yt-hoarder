// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { TaskFeed, TaskView } from "../api"
import {
  ACTIVE_POLL_MS,
  formatDuration,
  IDLE_POLL_MS,
  MAX_BACKOFF_MS,
  NO_FEED_MESSAGE,
  startTaskList,
  taskDetail,
  type TaskList,
} from "./tasks"

const NOW = 1_800_000_000_000

function task(overrides: Partial<TaskView> = {}): TaskView {
  return {
    id: "t1",
    title: "A video",
    channel: "A channel",
    kind: "download",
    status: "IN_PROGRESS",
    percent: 42,
    etaSeconds: 90,
    phase: "VIDEO",
    message: null,
    errorCode: null,
    retryAtMs: null,
    sleepUntilMs: null,
    ...overrides,
  }
}

const ok = (tasks: TaskView[] = [], hasMore = false): TaskFeed => ({ ok: true, tasks, hasMore })
const failed = (message: string, fatal: boolean, retryAfterSeconds: number | null = null): TaskFeed => ({
  ok: false,
  message,
  fatal,
  retryAfterSeconds,
})

type Listing = () => Promise<TaskFeed | undefined>

let running: TaskList | undefined

function start(listTasks: Listing) {
  document.body.innerHTML =
    '<section id="tasks" hidden><p id="tasks-status"></p><ul id="tasks-list"></ul></section>'
  const section = document.getElementById("tasks") as HTMLElement
  const status = document.getElementById("tasks-status") as HTMLElement
  const list = document.getElementById("tasks-list") as HTMLElement
  running = startTaskList({ section, status, list }, { listTasks, now: () => NOW })
  return { section, status, list, tasks: running }
}

const flush = () => vi.advanceTimersByTimeAsync(0)
const rowTexts = (list: HTMLElement) =>
  [...list.querySelectorAll("li")].map((row) => row.textContent)

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  running?.stop()
  running = undefined
  vi.useRealTimers()
  vi.restoreAllMocks()
  document.body.innerHTML = ""
})

describe("what the list shows", () => {
  it("is visible at once and says it is loading", () => {
    const { section, status } = start(() => new Promise(() => undefined))
    expect(section.hidden).toBe(false)
    expect(status.textContent).toBe("Loading…")
  })

  it("draws one row per task", async () => {
    const { list, status } = start(async () =>
      ok([task(), task({ id: "t2", title: "Another", status: "QUEUED", percent: 0 })])
    )
    await flush()
    expect(rowTexts(list)).toEqual(["A videoVideo · 42% · 1m 30s left", "AnotherQueued"])
    expect(status.hidden).toBe(true)
  })

  it("says so when nothing is running", async () => {
    const { list, status } = start(async () => ok([]))
    await flush()
    expect(list.children).toHaveLength(0)
    expect(status.textContent).toBe("Nothing running.")
    expect(status.hidden).toBe(false)
  })

  it("says when the list was cut short", async () => {
    const { status } = start(async () => ok([task()], true))
    await flush()
    expect(status.textContent).toBe("Showing the most recent tasks only.")
  })

  it("shows a progress bar only for a running download with a percentage", async () => {
    const { list } = start(async () =>
      ok([
        task({ id: "running" }),
        task({ id: "unknown", percent: null }),
        task({ id: "queued", status: "QUEUED" }),
        task({ id: "done", status: "COMPLETE" }),
        task({ id: "asleep", sleepUntilMs: NOW + 5000 }),
      ])
    )
    await flush()
    const bars = [...list.querySelectorAll("progress")]
    expect(bars).toHaveLength(1)
    expect(bars[0].value).toBe(42)
    expect(bars[0].max).toBe(100)
  })

  it("marks each row's state for styling", async () => {
    const { list } = start(async () =>
      ok([
        task({ id: "a" }),
        task({ id: "b", status: "QUEUED" }),
        task({ id: "c", status: "COMPLETE" }),
        task({ id: "d", status: "FAILED" }),
      ])
    )
    await flush()
    expect([...list.querySelectorAll<HTMLElement>("li")].map((row) => row.dataset.state)).toEqual([
      "active",
      "waiting",
      "done",
      "failed",
    ])
  })

  it("puts everything the server sent in as text, never markup", async () => {
    const { list } = start(async () =>
      ok([
        task({
          title: '<img src=x onerror="alert(1)">',
          channel: "<b>bold</b>",
          status: "FAILED",
          message: "<script>alert(2)</script>",
        }),
      ])
    )
    await flush()
    expect(list.querySelector("img, b, script")).toBeNull()
    expect(list.textContent).toContain('<img src=x onerror="alert(1)">')
    expect(list.textContent).toContain("<script>alert(2)</script>")
  })

  it("names a task that has no title yet", async () => {
    const { list } = start(async () => ok([task({ title: null, status: "RESOLVING" })]))
    await flush()
    expect(list.querySelector(".task-title")?.textContent).toBe("Untitled")
  })

  it("keeps a scrolled list where it was", async () => {
    const listTasks = vi.fn<Listing>().mockResolvedValue(ok([task()]))
    const { list } = start(listTasks)
    await flush()
    list.scrollTop = 40
    await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS)
    expect(listTasks).toHaveBeenCalledTimes(2)
    expect(list.scrollTop).toBe(40)
  })
})

describe("when it asks", () => {
  it("every few seconds while anything is active", async () => {
    const listTasks = vi.fn<Listing>().mockResolvedValue(ok([task()]))
    start(listTasks)
    await flush()
    expect(listTasks).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS - 1)
    expect(listTasks).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(listTasks).toHaveBeenCalledTimes(2)
  })

  it("less often when everything has finished", async () => {
    const listTasks = vi.fn<Listing>().mockResolvedValue(ok([task({ status: "COMPLETE" })]))
    start(listTasks)
    await flush()
    await vi.advanceTimersByTimeAsync(IDLE_POLL_MS - 1)
    expect(listTasks).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(listTasks).toHaveBeenCalledTimes(2)
  })

  it.each(["RETRY", "NOT_READY"] as const)("less often when the only waiting task is %s", async (status) => {
    const listTasks = vi.fn<Listing>().mockResolvedValue(ok([task({ status })]))
    start(listTasks)
    await flush()
    await vi.advanceTimersByTimeAsync(IDLE_POLL_MS - 1)
    expect(listTasks).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(listTasks).toHaveBeenCalledTimes(2)
  })

  it("never has two requests in flight, but asks once more afterwards", async () => {
    let release: (feed: TaskFeed) => void = () => undefined
    const listTasks = vi
      .fn<Listing>()
      .mockImplementationOnce(() => new Promise((resolve) => (release = resolve)))
      .mockResolvedValue(ok([]))
    const { tasks } = start(listTasks)

    tasks.refresh()
    tasks.refresh()
    await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS * 3)
    expect(listTasks).toHaveBeenCalledTimes(1)

    release(ok([task()]))
    await flush()
    expect(listTasks).toHaveBeenCalledTimes(2)
  })

  it("asks again at once on refresh, without waiting for the timer", async () => {
    const listTasks = vi.fn<Listing>().mockResolvedValue(ok([task()]))
    const { tasks } = start(listTasks)
    await flush()
    tasks.refresh()
    await flush()
    expect(listTasks).toHaveBeenCalledTimes(2)
  })

  it("pauses while the popup is hidden and catches up when it is shown", async () => {
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false)
    const listTasks = vi.fn<Listing>().mockResolvedValue(ok([task()]))
    start(listTasks)
    await flush()

    hidden.mockReturnValue(true)
    document.dispatchEvent(new Event("visibilitychange"))
    await vi.advanceTimersByTimeAsync(MAX_BACKOFF_MS)
    expect(listTasks).toHaveBeenCalledTimes(1)

    hidden.mockReturnValue(false)
    document.dispatchEvent(new Event("visibilitychange"))
    await flush()
    expect(listTasks).toHaveBeenCalledTimes(2)
  })

  it("stops for good when the page is hidden for the last time", async () => {
    const listTasks = vi.fn<Listing>().mockResolvedValue(ok([task()]))
    const { tasks } = start(listTasks)
    await flush()

    window.dispatchEvent(new Event("pagehide"))
    tasks.refresh()
    await vi.advanceTimersByTimeAsync(MAX_BACKOFF_MS)
    expect(listTasks).toHaveBeenCalledTimes(1)
  })
})

describe("when it goes wrong", () => {
  it("backs off after each failure: 3s, 6s, 12s", async () => {
    const listTasks = vi.fn<Listing>().mockResolvedValue(failed("The server hit an error.", false))
    start(listTasks)
    await flush()
    expect(listTasks).toHaveBeenCalledTimes(1)

    for (const [wait, calls] of [
      [3_000, 2],
      [6_000, 3],
      [12_000, 4],
    ] as const) {
      await vi.advanceTimersByTimeAsync(wait - 1)
      expect(listTasks).toHaveBeenCalledTimes(calls - 1)
      await vi.advanceTimersByTimeAsync(1)
      expect(listTasks).toHaveBeenCalledTimes(calls)
    }
  })

  it("never waits longer than the cap between tries", async () => {
    const listTasks = vi.fn<Listing>().mockResolvedValue(failed("down", false))
    start(listTasks)
    await vi.advanceTimersByTimeAsync(10 * MAX_BACKOFF_MS)
    const before = listTasks.mock.calls.length
    await vi.advanceTimersByTimeAsync(MAX_BACKOFF_MS)
    expect(listTasks.mock.calls.length).toBe(before + 1)
  })

  it("waits as long as a rate limit asks, if that is longer", async () => {
    const listTasks = vi.fn<Listing>().mockResolvedValue(failed("Too many requests.", false, 30))
    start(listTasks)
    await flush()
    await vi.advanceTimersByTimeAsync(29_999)
    expect(listTasks).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(listTasks).toHaveBeenCalledTimes(2)
  })

  it("keeps the rows it has and says it couldn't refresh", async () => {
    const listTasks = vi
      .fn<Listing>()
      .mockResolvedValueOnce(ok([task()]))
      .mockResolvedValue(failed("The server hit an error.", false))
    const { list, status } = start(listTasks)
    await flush()
    await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS)

    expect(rowTexts(list)).toHaveLength(1)
    expect(status.textContent).toBe("Couldn't refresh the list. The server hit an error.")
    expect(status.dataset.tone).toBe("muted")
  })

  it("recovers when the server answers again", async () => {
    const listTasks = vi
      .fn<Listing>()
      .mockResolvedValueOnce(failed("down", false))
      .mockResolvedValue(ok([task()]))
    const { status } = start(listTasks)
    await flush()
    await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS)

    expect(status.hidden).toBe(true)
    await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS)
    expect(listTasks).toHaveBeenCalledTimes(3)
  })

  it("stops asking after one fatal answer and shows why", async () => {
    const listTasks = vi.fn<Listing>().mockResolvedValue(failed("The server rejected the token.", true))
    const { list, status, tasks } = start(listTasks)
    await flush()

    tasks.refresh()
    await vi.advanceTimersByTimeAsync(10 * MAX_BACKOFF_MS)
    expect(listTasks).toHaveBeenCalledTimes(1)
    expect(status.textContent).toBe("The server rejected the token.")
    expect(status.dataset.tone).toBe("error")
    expect(list.children).toHaveLength(0)
  })

  it("treats no answer from the background as a failure to retry", async () => {
    const listTasks = vi.fn<Listing>().mockResolvedValue(undefined)
    const { status } = start(listTasks)
    await flush()
    expect(status.textContent).toContain(NO_FEED_MESSAGE)
    await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS)
    expect(listTasks).toHaveBeenCalledTimes(2)
  })

  it("treats a rejected message the same way", async () => {
    const listTasks = vi.fn<Listing>().mockRejectedValue(new Error("Extension context invalidated."))
    const { status } = start(listTasks)
    await flush()
    expect(status.textContent).toContain(NO_FEED_MESSAGE)
    await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS)
    expect(listTasks).toHaveBeenCalledTimes(2)
  })
})

describe("taskDetail", () => {
  it.each<[string, Partial<TaskView>, string]>([
    ["a running download", {}, "Video · 42% · 1m 30s left"],
    ["a running audio download", { phase: "AUDIO", etaSeconds: null }, "Audio · 42%"],
    ["a running job with nothing to report", { phase: null, percent: null, etaSeconds: null }, "Working…"],
    ["a transcript", { kind: "transcript", phase: null, percent: 10, etaSeconds: null }, "Transcript · 10%"],
    ["a clip", { kind: "clip", status: "QUEUED" }, "Clip · Queued"],
    ["a placeholder", { status: "RESOLVING" }, "Looking up…"],
    ["a queued task", { status: "QUEUED" }, "Queued"],
    ["a queued task that sleeps before starting", { status: "QUEUED", sleepUntilMs: NOW + 45_000 }, "Starts in 45s"],
    ["a download that sleeps before starting", { sleepUntilMs: NOW + 45_000 }, "Starts in 45s"],
    ["a sleep that has ended", { sleepUntilMs: NOW - 1 }, "Video · 42% · 1m 30s left"],
    ["post-processing", { status: "POSTPROCESSING" }, "Processing…"],
    ["a retry that is scheduled", { status: "RETRY", retryAtMs: NOW + 180_000 }, "Retrying in 3m"],
    [
      "a retry with a reason",
      { status: "RETRY", retryAtMs: NOW + 180_000, errorCode: "RATE_LIMITED" },
      "Retrying in 3m · RATE_LIMITED",
    ],
    ["a retry that is due", { status: "RETRY", retryAtMs: NOW - 1 }, "Retrying soon"],
    ["a video not out yet", { status: "NOT_READY" }, "Not released yet"],
    ["a finished task", { status: "COMPLETE" }, "Done"],
    ["a finished task with an old sleep", { status: "COMPLETE", sleepUntilMs: NOW + 45_000 }, "Done"],
    ["a failure with no reason", { status: "FAILED" }, "Failed"],
    ["a failure with a message", { status: "FAILED", message: "boom" }, "Failed · boom"],
    ["a failure with a code and a message", { status: "FAILED", message: "boom", errorCode: "E1" }, "Failed · E1"],
  ])("%s", (_name, overrides, expected) => {
    expect(taskDetail(task(overrides), NOW)).toBe(expected)
  })
})

describe("formatDuration", () => {
  it.each([
    [0, "0s"],
    [-5, "0s"],
    [45, "45s"],
    [59.6, "1m"],
    [60, "1m"],
    [90, "1m 30s"],
    [3599, "59m 59s"],
    [3600, "1h"],
    [5400, "1h 30m"],
  ])("%d → %s", (seconds, text) => {
    expect(formatDuration(seconds)).toBe(text)
  })
})
