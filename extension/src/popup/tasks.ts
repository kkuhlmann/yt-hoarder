import type { TaskFeed, TaskKind, TaskView } from "../api"

export type TaskListDeps = {
  /** Resolves to nothing when the background script didn't answer. */
  listTasks(): Promise<TaskFeed | undefined>
  now(): number
}

export type TaskListElements = {
  section: HTMLElement
  status: HTMLElement
  list: HTMLElement
}

export type TaskList = {
  /** Asks again right away, for after something was submitted. */
  refresh(): void
  stop(): void
}

export const ACTIVE_POLL_MS = 3_000
export const IDLE_POLL_MS = 10_000
export const MAX_BACKOFF_MS = 60_000
export const NO_FEED_MESSAGE = "The extension's background script didn't answer."

const KIND_LABELS: Record<TaskKind, string> = {
  download: "",
  transcript: "Transcript",
  clip: "Clip",
  conversion: "Conversion",
}

function isActive(task: TaskView): boolean {
  return task.status !== "COMPLETE" && task.status !== "FAILED"
}

// RETRY and NOT_READY are active but can wait hours or days, so they poll at the idle cadence.
function movesSoon(task: TaskView): boolean {
  return (
    task.status === "RESOLVING" ||
    task.status === "QUEUED" ||
    task.status === "IN_PROGRESS" ||
    task.status === "POSTPROCESSING"
  )
}

function taskState(task: TaskView): "active" | "waiting" | "done" | "failed" {
  switch (task.status) {
    case "COMPLETE":
      return "done"
    case "FAILED":
      return "failed"
    case "IN_PROGRESS":
    case "POSTPROCESSING":
      return "active"
    default:
      return "waiting"
  }
}

export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return total % 60 ? `${minutes}m ${total % 60}s` : `${minutes}m`
  const hours = Math.floor(minutes / 60)
  return minutes % 60 ? `${hours}h ${minutes % 60}m` : `${hours}h`
}

function statusParts(task: TaskView, now: number): string[] {
  if (isActive(task) && task.sleepUntilMs !== null && task.sleepUntilMs > now) {
    return [`Starts in ${formatDuration((task.sleepUntilMs - now) / 1000)}`]
  }
  switch (task.status) {
    case "RESOLVING":
      return ["Looking up…"]
    case "QUEUED":
      return ["Queued"]
    case "IN_PROGRESS": {
      const parts: string[] = []
      if (task.phase) parts.push(task.phase === "VIDEO" ? "Video" : "Audio")
      if (task.percent !== null) parts.push(`${task.percent}%`)
      if (task.etaSeconds !== null) parts.push(`${formatDuration(task.etaSeconds)} left`)
      return parts.length > 0 ? parts : ["Working…"]
    }
    case "POSTPROCESSING":
      return ["Processing…"]
    case "RETRY": {
      const when =
        task.retryAtMs !== null && task.retryAtMs > now
          ? `Retrying in ${formatDuration((task.retryAtMs - now) / 1000)}`
          : "Retrying soon"
      return task.errorCode ? [when, task.errorCode] : [when]
    }
    case "NOT_READY":
      return ["Not released yet"]
    case "COMPLETE":
      return ["Done"]
    case "FAILED": {
      const reason = task.errorCode ?? task.message
      return reason ? ["Failed", reason] : ["Failed"]
    }
  }
}

/** The one line under a task's title: what it's doing and how far along it is. */
export function taskDetail(task: TaskView, now: number): string {
  const label = KIND_LABELS[task.kind]
  return [...(label ? [label] : []), ...statusParts(task, now)].join(" · ")
}

function taskRow(doc: Document, task: TaskView, now: number): HTMLLIElement {
  const row = doc.createElement("li")
  row.className = "task"
  row.dataset.state = taskState(task)
  if (task.channel) row.title = task.channel

  // Everything the server sent goes in as text, never as markup.
  const title = doc.createElement("span")
  title.className = "task-title"
  title.textContent = task.title ?? "Untitled"
  const detail = doc.createElement("span")
  detail.className = "task-detail"
  detail.textContent = taskDetail(task, now)
  detail.title = detail.textContent
  row.append(title, detail)

  const sleeping = task.sleepUntilMs !== null && task.sleepUntilMs > now
  if (task.status === "IN_PROGRESS" && task.percent !== null && !sleeping) {
    const bar = doc.createElement("progress")
    bar.max = 100
    bar.value = task.percent
    bar.setAttribute("aria-label", `Progress of ${title.textContent}`)
    row.append(bar)
  }
  return row
}

/**
 * Keeps the popup's task list current for as long as the popup is open.
 *
 * It polls with one request at a time: SSE can't carry the token, and the background script can't
 * hold a stream open. Closing the popup destroys this page, so nothing outlives it.
 */
export function startTaskList(elements: TaskListElements, deps: TaskListDeps): TaskList {
  const { section, status, list } = elements
  const doc = list.ownerDocument
  const win = doc.defaultView

  let timer: ReturnType<typeof setTimeout> | undefined
  let inFlight = false
  let askAgain = false
  let stopped = false
  let failures = 0
  let latest: { tasks: TaskView[]; hasMore: boolean } | null = null
  let problem: { message: string; fatal: boolean } | null = null

  function render() {
    // Replacing the rows would otherwise snap a scrolled list back to the top on every poll.
    const scrolled = list.scrollTop
    const now = deps.now()
    list.replaceChildren(...(latest?.tasks ?? []).map((task) => taskRow(doc, task, now)))
    list.scrollTop = scrolled

    let text = ""
    if (problem) text = problem.fatal ? problem.message : `Couldn't refresh the list. ${problem.message}`
    else if (latest === null) text = "Loading…"
    else if (latest.tasks.length === 0) text = "Nothing running."
    else if (latest.hasMore) text = "Showing the most recent tasks only."
    status.textContent = text
    status.hidden = text === ""
    status.dataset.tone = problem?.fatal ? "error" : "muted"
  }

  function schedule(delay: number) {
    clearTimeout(timer)
    timer = stopped || doc.hidden ? undefined : setTimeout(() => void poll(), delay)
  }

  function stop() {
    stopped = true
    clearTimeout(timer)
    doc.removeEventListener("visibilitychange", onVisibility)
    win?.removeEventListener("pagehide", stop)
  }

  function onVisibility() {
    if (doc.hidden) clearTimeout(timer)
    else void poll()
  }

  async function poll() {
    if (stopped) return
    if (inFlight) {
      askAgain = true
      return
    }
    clearTimeout(timer)
    inFlight = true
    let feed: TaskFeed | undefined
    try {
      feed = await deps.listTasks()
    } catch {
      feed = undefined
    }
    inFlight = false
    if (stopped) return

    let delay: number
    if (feed?.ok) {
      failures = 0
      problem = null
      latest = { tasks: feed.tasks, hasMore: feed.hasMore }
      delay = askAgain ? 0 : feed.tasks.some(movesSoon) ? ACTIVE_POLL_MS : IDLE_POLL_MS
    } else if (feed && feed.fatal) {
      // Asking again can't help, and each refused token counts against the server's limit on
      // failed sign-ins from this address.
      problem = { message: feed.message, fatal: true }
      latest = null
      render()
      stop()
      return
    } else {
      failures += 1
      problem = { message: feed ? feed.message : NO_FEED_MESSAGE, fatal: false }
      const asked = (feed?.retryAfterSeconds ?? 0) * 1000
      delay = Math.max(asked, Math.min(MAX_BACKOFF_MS, ACTIVE_POLL_MS * 2 ** (failures - 1)))
    }
    askAgain = false
    render()
    schedule(delay)
  }

  section.hidden = false
  render()
  doc.addEventListener("visibilitychange", onVisibility)
  win?.addEventListener("pagehide", stop)
  void poll()

  return { refresh: () => void poll(), stop }
}
