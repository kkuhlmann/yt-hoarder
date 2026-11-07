// @vitest-environment jsdom
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { TaskFeed, TaskView } from "../api"
import type { ExtensionRequest } from "../messages"
import { DEFAULT_PREFS, type Prefs } from "../settings"
import { DEFAULT_OPTIONS, NO_FILTERS } from "../testing"
import { BAD_LENGTH_MESSAGE, initPopup, LENGTHS_SWAPPED_MESSAGE, NO_REPLY, type PopupDeps } from "./view"

const HTML = readFileSync(join(import.meta.dirname, "popup.html"), "utf8")

const WATCH = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
const WATCH_IN_LIST = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123"
const PLAYLIST = "https://www.youtube.com/playlist?list=PL123"
const CHANNEL = "https://www.youtube.com/@RickAstleyYT/videos"

function render() {
  const parsed = new DOMParser().parseFromString(HTML, "text/html")
  document.body.innerHTML = parsed.body.innerHTML
}

function deps(overrides: Partial<PopupDeps> = {}) {
  const sent: ExtensionRequest[] = []
  const saved: Prefs[] = []
  const listed = { count: 0 }
  const value: PopupDeps = {
    activeTabUrl: async () => WATCH,
    isConfigured: async () => true,
    loadPrefs: async () => structuredClone(DEFAULT_PREFS),
    savePrefs: async (prefs) => {
      saved.push(structuredClone(prefs))
    },
    send: async (message) => {
      sent.push(message)
      return { tone: "success", message: "Queued." }
    },
    listTasks: async () => {
      listed.count += 1
      return { ok: true, tasks: [], hasMore: false }
    },
    openOptions: vi.fn(),
    ...overrides,
  }
  return { deps: value, sent, saved, listed }
}

const el = (id: string) => document.getElementById(id) as HTMLElement
const input = (id: string) => el(id) as HTMLInputElement
const select = (id: string) => el(id) as HTMLSelectElement
const visible = (id: string) => !el(id).hidden
const radio = (name: string, value: string) =>
  document.querySelector<HTMLInputElement>(`input[name="${name}"][value="${value}"]`) as HTMLInputElement

function choose(name: string, value: string) {
  const input = radio(name, value)
  input.checked = true
  input.dispatchEvent(new Event("change"))
}

async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0))
}

beforeEach(render)
afterEach(() => {
  // Ends the task list's polling, as closing the popup does.
  window.dispatchEvent(new Event("pagehide"))
  document.body.innerHTML = ""
  vi.restoreAllMocks()
})

describe("which sections show", () => {
  it("only the setup prompt before the extension is configured", async () => {
    const { deps: d } = deps({ isConfigured: async () => false })
    await initPopup(document, d)
    expect(visible("setup")).toBe(true)
    expect(visible("download")).toBe(false)
    expect(visible("subscribe")).toBe(false)
    el("setup-button").click()
    expect(d.openOptions).toHaveBeenCalled()
  })

  it("a video page offers a download and points to the channel for subscribing", async () => {
    await initPopup(document, deps({ activeTabUrl: async () => WATCH }).deps)
    expect(visible("download")).toBe(true)
    expect(visible("download-scope")).toBe(false)
    expect(visible("subscribe")).toBe(false)
    expect(visible("subscribe-hint")).toBe(true)
    expect(el("subscribe-hint").textContent).toBe("To subscribe, open the channel's page.")
    expect(visible("unsupported")).toBe(false)
  })

  it("a video in a playlist offers this video or the whole playlist, and subscribing to the playlist", async () => {
    await initPopup(document, deps({ activeTabUrl: async () => WATCH_IN_LIST }).deps)
    expect(visible("tabs")).toBe(true)
    expect(visible("download")).toBe(true)
    expect(visible("download-scope")).toBe(true)
    expect(visible("subscribe")).toBe(false)
    expect(el("subscribe-url").textContent).toBe(PLAYLIST)
    expect(visible("subscribe-hint")).toBe(false)
  })

  it("a playlist page downloads or subscribes to the playlist", async () => {
    await initPopup(document, deps({ activeTabUrl: async () => PLAYLIST }).deps)
    expect(visible("tabs")).toBe(true)
    expect(visible("download")).toBe(true)
    expect(visible("download-scope")).toBe(false)
    expect(el("download-button").textContent).toBe("Download playlist")
    expect(visible("subscribe")).toBe(false)
  })

  it("a page with a single action has no tab bar", async () => {
    await initPopup(document, deps({ activeTabUrl: async () => WATCH }).deps)
    expect(visible("tabs")).toBe(false)
    document.body.innerHTML = ""
    render()
    await initPopup(document, deps({ activeTabUrl: async () => CHANNEL }).deps)
    expect(visible("tabs")).toBe(false)
  })

  it("switching tabs shows one panel at a time and remembers the choice", async () => {
    const { deps: d, saved } = deps({ activeTabUrl: async () => PLAYLIST })
    await initPopup(document, d)
    el("tab-subscribe").click()
    expect(visible("subscribe")).toBe(true)
    expect(visible("download")).toBe(false)
    expect(el("tab-subscribe").getAttribute("aria-selected")).toBe("true")
    expect(el("tab-download").getAttribute("aria-selected")).toBe("false")
    expect(saved.at(-1)?.lastTab).toBe("subscribe")
  })

  it("opens on the tab used last", async () => {
    const loadPrefs = async () => ({ ...structuredClone(DEFAULT_PREFS), lastTab: "subscribe" as const })
    await initPopup(document, deps({ activeTabUrl: async () => PLAYLIST, loadPrefs }).deps)
    expect(visible("subscribe")).toBe(true)
    expect(visible("download")).toBe(false)
  })

  it("arrow keys move between tabs", async () => {
    await initPopup(document, deps({ activeTabUrl: async () => PLAYLIST }).deps)
    el("tab-download").dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight" }))
    expect(visible("subscribe")).toBe(true)
    el("tab-subscribe").dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft" }))
    expect(visible("download")).toBe(true)
  })

  it("a bad length opens the collapsed filters", async () => {
    await initPopup(document, deps({ activeTabUrl: async () => CHANNEL }).deps)
    const filters = el("subscribe-filters") as HTMLDetailsElement
    expect(filters.open).toBe(false)
    input("subscribe-min").value = "9"
    input("subscribe-max").value = "2"
    el("subscribe-button").click()
    expect(filters.open).toBe(true)
  })

  it("a channel page only subscribes", async () => {
    await initPopup(document, deps({ activeTabUrl: async () => CHANNEL }).deps)
    expect(visible("download")).toBe(false)
    expect(visible("subscribe")).toBe(true)
    expect(el("subscribe-url").textContent).toBe("https://www.youtube.com/@RickAstleyYT/videos")
    expect(visible("subscribe-hint")).toBe(false)
  })

  it("any other page says what to open", async () => {
    await initPopup(document, deps({ activeTabUrl: async () => "https://example.com/" }).deps)
    expect(visible("unsupported")).toBe(true)
    expect(visible("download")).toBe(false)
    expect(visible("subscribe")).toBe(false)
    expect(visible("subscribe-hint")).toBe(false)
  })

  it("a tab without a readable URL counts as unsupported", async () => {
    await initPopup(document, deps({ activeTabUrl: async () => undefined }).deps)
    expect(visible("unsupported")).toBe(true)
  })
})

describe("options", () => {
  it("applies remembered choices", async () => {
    const prefs: Prefs = {
      download: { mediaType: "AUDIO", transcribe: true, videoQuality: "720P", audioQuality: "96K" },
      subscribe: { mediaType: "TRANSCRIPT", transcribe: false, videoQuality: "BEST", audioQuality: "BEST" },
      lastTab: "download",
    }
    await initPopup(document, deps({ activeTabUrl: async () => WATCH_IN_LIST, loadPrefs: async () => prefs }).deps)
    expect(radio("download-mode", "AUDIO").checked).toBe(true)
    expect(input("download-transcribe").checked).toBe(true)
    expect(select("download-video-quality").value).toBe("720P")
    expect(select("download-audio-quality").value).toBe("96K")
    expect(radio("subscribe-mode", "TRANSCRIPT").checked).toBe(true)
    expect(visible("subscribe-transcribe-row")).toBe(false)
  })

  it("offers every quality the app does", async () => {
    await initPopup(document, deps().deps)
    const labels = (id: string) => [...select(id).options].map((option) => option.textContent)
    expect(labels("download-video-quality")).toEqual(["Best", "1440p", "1080p", "720p", "480p", "360p"])
    expect(labels("download-audio-quality")).toEqual(["Best", "128 kbps", "96 kbps", "64 kbps", "48 kbps"])
  })

  it("shows the quality, and transcribe, that the chosen type uses", async () => {
    await initPopup(document, deps().deps)
    expect(visible("download-quality")).toBe(true)
    expect(visible("download-video-quality-row")).toBe(true)
    expect(visible("download-audio-quality-row")).toBe(false)
    expect(visible("download-transcribe-row")).toBe(true)

    choose("download-mode", "AUDIO")
    expect(visible("download-quality")).toBe(true)
    expect(visible("download-video-quality-row")).toBe(false)
    expect(visible("download-audio-quality-row")).toBe(true)

    choose("download-mode", "TRANSCRIPT")
    expect(visible("download-quality")).toBe(false)
    expect(visible("download-video-quality-row")).toBe(false)
    expect(visible("download-audio-quality-row")).toBe(false)
    expect(visible("download-transcribe-row")).toBe(false)
  })

  it("offers Overwrite on downloads only", async () => {
    await initPopup(document, deps({ activeTabUrl: async () => WATCH_IN_LIST }).deps)
    expect(document.getElementById("download-overwrite")).not.toBeNull()
    expect(document.getElementById("subscribe-overwrite")).toBeNull()
  })

  it("starts every popup with Overwrite off and no subscription filters", async () => {
    await initPopup(document, deps({ activeTabUrl: async () => WATCH_IN_LIST }).deps)
    expect(input("download-overwrite").checked).toBe(false)
    for (const id of ["subscribe-filter", "subscribe-after", "subscribe-min", "subscribe-max"]) {
      expect(input(id).value).toBe("")
    }
  })
})

describe("submitting", () => {
  it("sends a download, remembers the choice, and shows the reply", async () => {
    const { deps: d, sent, saved } = deps()
    await initPopup(document, d)
    choose("download-mode", "AUDIO")
    input("download-transcribe").checked = true
    el("download-button").click()
    await settle()

    expect(sent).toEqual([
      {
        type: "download",
        request: {
          ...DEFAULT_OPTIONS,
          url: WATCH,
          mediaType: "AUDIO",
          transcribe: true,
          overwrite: false,
          wholePlaylist: false,
        },
      },
    ])
    expect(saved.at(-1)?.download).toEqual({
      mediaType: "AUDIO",
      transcribe: true,
      videoQuality: "BEST",
      audioQuality: "BEST",
    })
    expect(el("result").textContent).toBe("Queued.")
    expect(el("result").dataset.tone).toBe("success")
    expect((el("download-button") as HTMLButtonElement).disabled).toBe(false)
  })

  it("sends the chosen quality and overwrite, remembering the quality but never overwrite", async () => {
    const { deps: d, sent, saved } = deps()
    await initPopup(document, d)
    select("download-video-quality").value = "1080P"
    input("download-overwrite").checked = true
    el("download-button").click()
    await settle()

    expect(sent[0]).toMatchObject({ request: { videoQuality: "1080P", overwrite: true } })
    expect(saved.at(-1)?.download).toEqual({ ...DEFAULT_PREFS.download, videoQuality: "1080P" })
    expect(saved.at(-1)?.download).not.toHaveProperty("overwrite")
  })

  it("downloads the whole playlist when asked", async () => {
    const { deps: d, sent } = deps({ activeTabUrl: async () => WATCH_IN_LIST })
    await initPopup(document, d)
    choose("download-scope", "playlist")
    el("download-button").click()
    await settle()
    expect(sent[0]).toMatchObject({ request: { url: PLAYLIST, wholePlaylist: true } })
  })

  it("a playlist page downloads the playlist", async () => {
    const { deps: d, sent } = deps({ activeTabUrl: async () => PLAYLIST })
    await initPopup(document, d)
    el("download-button").click()
    await settle()
    expect(sent[0]).toMatchObject({ request: { url: PLAYLIST, wholePlaylist: true } })
  })

  it("sends a subscription with every option and filter", async () => {
    const { deps: d, sent, saved } = deps({ activeTabUrl: async () => CHANNEL })
    await initPopup(document, d)
    choose("subscribe-mode", "AUDIO")
    select("subscribe-audio-quality").value = "64K"
    input("subscribe-transcribe").checked = true
    input("subscribe-filter").value = "podcast"
    input("subscribe-after").value = "2026-01-15"
    input("subscribe-min").value = "1.5"
    input("subscribe-max").value = "60"
    el("subscribe-button").click()
    await settle()

    expect(sent).toEqual([
      {
        type: "subscribe",
        request: {
          url: "https://www.youtube.com/@RickAstleyYT/videos",
          mediaType: "AUDIO",
          transcribe: true,
          videoQuality: "BEST",
          audioQuality: "64K",
          titleFilter: "podcast",
          uploadedAfter: new Date(2026, 0, 15).toISOString(),
          minDurationSeconds: 90,
          maxDurationSeconds: 3600,
        },
      },
    ])
    expect(saved.at(-1)?.subscribe).toEqual({
      mediaType: "AUDIO",
      transcribe: true,
      videoQuality: "BEST",
      audioQuality: "64K",
    })
  })

  it("a subscription without filters sends none", async () => {
    const { deps: d, sent } = deps({ activeTabUrl: async () => CHANNEL })
    await initPopup(document, d)
    el("subscribe-button").click()
    await settle()
    expect(sent[0]).toEqual({
      type: "subscribe",
      request: { ...DEFAULT_OPTIONS, ...NO_FILTERS, url: "https://www.youtube.com/@RickAstleyYT/videos" },
    })
  })

  it("refuses a length box holding something that isn't a number", async () => {
    const { deps: d, sent } = deps({ activeTabUrl: async () => CHANNEL })
    await initPopup(document, d)
    // What a browser reports for typed garbage: an empty value and badInput. jsdom can't type.
    Object.defineProperty(input("subscribe-min"), "validity", { value: { badInput: true } })
    el("subscribe-button").click()
    await settle()
    expect(sent).toEqual([])
    expect(el("result").textContent).toBe(BAD_LENGTH_MESSAGE)
  })

  it.each([
    ["", "-5", BAD_LENGTH_MESSAGE],
    ["30", "10", LENGTHS_SWAPPED_MESSAGE],
  ])("refuses lengths %j to %j without sending", async (min, max, message) => {
    const { deps: d, sent } = deps({ activeTabUrl: async () => CHANNEL })
    await initPopup(document, d)
    input("subscribe-min").value = min
    input("subscribe-max").value = max
    el("subscribe-button").click()
    await settle()
    expect(sent).toEqual([])
    expect(el("result").textContent).toBe(message)
    expect(el("result").dataset.tone).toBe("error")
  })

  it("shows an error when the background script doesn't answer", async () => {
    const { deps: d } = deps({ send: async () => undefined })
    await initPopup(document, d)
    el("download-button").click()
    await settle()
    expect(el("result").textContent).toBe(NO_REPLY.message)
    expect(el("result").dataset.tone).toBe("error")
  })
})

describe("the task list", () => {
  const TASK: TaskView = {
    id: "t1",
    title: "Never Gonna Give You Up",
    channel: "Rick Astley",
    kind: "download",
    status: "IN_PROGRESS",
    percent: 42,
    etaSeconds: 30,
    phase: "VIDEO",
    message: null,
    errorCode: null,
    retryAtMs: null,
    sleepUntilMs: null,
  }
  const feed = (...tasks: TaskView[]): TaskFeed => ({ ok: true, tasks, hasMore: false })
  const rows = () => [...document.querySelectorAll("#tasks-list li")]

  it("is not shown before the extension is configured", async () => {
    const { deps: d, listed } = deps({ isConfigured: async () => false })
    await initPopup(document, d)
    await settle()
    expect(visible("tasks")).toBe(false)
    expect(listed.count).toBe(0)
  })

  it("shows the user's tasks inside the popup", async () => {
    await initPopup(document, deps({ listTasks: async () => feed(TASK) }).deps)
    await settle()
    expect(visible("tasks")).toBe(true)
    expect(rows()).toHaveLength(1)
    expect(rows()[0].textContent).toContain("Never Gonna Give You Up")
    expect(rows()[0].textContent).toContain("42%")
    expect(document.querySelector<HTMLProgressElement>("#tasks-list progress")?.value).toBe(42)
  })

  it("shows even on a page the extension can't act on", async () => {
    const { deps: d } = deps({
      activeTabUrl: async () => "https://example.com/",
      listTasks: async () => feed(TASK),
    })
    await initPopup(document, d)
    await settle()
    expect(visible("unsupported")).toBe(true)
    expect(rows()).toHaveLength(1)
  })

  it("says so when nothing is running", async () => {
    await initPopup(document, deps().deps)
    await settle()
    expect(el("tasks-status").textContent).toBe("Nothing running.")
  })

  it("explains a fatal failure and keeps the rest of the popup working", async () => {
    const message = "The server rejected the token."
    const { deps: d, sent } = deps({
      listTasks: async () => ({ ok: false, message, fatal: true, retryAfterSeconds: null }),
    })
    await initPopup(document, d)
    await settle()
    expect(el("tasks-status").textContent).toBe(message)
    el("download-button").click()
    await settle()
    expect(sent).toHaveLength(1)
  })

  it("asks again right after something is submitted", async () => {
    const { deps: d, listed } = deps()
    await initPopup(document, d)
    await settle()
    expect(listed.count).toBe(1)

    el("download-button").click()
    await settle()
    expect(listed.count).toBe(2)
  })

  it("does not put task text into the result line", async () => {
    await initPopup(document, deps({ listTasks: async () => feed(TASK) }).deps)
    await settle()
    expect(el("result").textContent).toBe("")
  })
})
