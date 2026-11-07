import { afterEach, describe, expect, it, vi } from "vitest"
import { createHandler, SETUP_MESSAGE } from "./handler"
import type { ExtensionRequest } from "./messages"
import { createSettingsStore } from "./settings"
import {
  DEFAULT_OPTIONS,
  fakeStorageArea,
  htmlResponse,
  jsonResponse,
  NO_FILTERS,
  redirectResponse,
  routedFetch,
  TOKEN,
} from "./testing"

const API = "https://h/api"
const DOWNLOAD: ExtensionRequest = {
  type: "download",
  request: {
    ...DEFAULT_OPTIONS,
    url: "https://www.youtube.com/watch?v=x",
    mediaType: "AUDIO",
    transcribe: true,
    overwrite: false,
    wholePlaylist: false,
  },
}
const SUBSCRIBE: ExtensionRequest = {
  type: "subscribe",
  request: { ...DEFAULT_OPTIONS, ...NO_FILTERS, url: "https://www.youtube.com/@h" },
}
const IDENTITY = {
  username: "kim",
  token_name: "laptop",
  token_prefix: "ytdlh_SuperS",
  expires_at: null,
  app_version: "0.3.0",
}

function setup(
  routes: Parameters<typeof routedFetch>[0],
  stored: Record<string, unknown> = { serverUrl: "https://h", apiBase: API, token: TOKEN },
  granted = true
) {
  const area = fakeStorageArea(stored)
  const { fetchImpl, calls } = routedFetch(routes)
  const hasPermission = vi.fn(async () => granted)
  const handle = createHandler({ settings: createSettingsStore(area), hasPermission, fetch: fetchImpl })
  return { handle, calls, area, hasPermission }
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe("download and subscribe", () => {
  it("posts a download to /ytdl/", async () => {
    const { handle, calls, hasPermission } = setup({ [`${API}/ytdl/`]: jsonResponse(201, { task_id: "t" }) })
    expect(await handle(DOWNLOAD)).toEqual({ tone: "success", message: "Queued. Follow it in YT-Hoarder's Tasks tab." })
    expect(hasPermission).toHaveBeenCalledWith("https://h/*")
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toBe(`${API}/ytdl/`)
    expect(JSON.parse(calls[0].init.body as string)).toEqual({
      url: "https://www.youtube.com/watch?v=x",
      media_type: "AUDIO",
      audio_only: true,
      generate_transcript: true,
      download_quality: "BEST",
      audio_quality: "BEST",
      overwrite: false,
      download_playlist: false,
    })
  })

  it("posts a subscription to /subscriptions", async () => {
    const { handle, calls } = setup({
      [`${API}/subscriptions`]: jsonResponse(201, { task: "DUPLICATE_SUBSCRIPTION" }),
    })
    expect(await handle(SUBSCRIBE)).toEqual({ tone: "info", message: "You're already subscribed to this." })
    expect(calls[0].url).toBe(`${API}/subscriptions`)
  })

  it("asks for setup before anything is configured", async () => {
    const { handle, calls } = setup({}, {})
    expect(await handle(DOWNLOAD)).toEqual({ tone: "error", message: SETUP_MESSAGE })
    expect(calls).toHaveLength(0)
  })

  it("says so when the host permission is missing", async () => {
    const { handle, calls } = setup({}, undefined, false)
    expect(await handle(SUBSCRIBE)).toEqual({
      tone: "error",
      message: "The extension isn't allowed to contact https://h yet. Open its settings and click Save & test.",
    })
    expect(calls).toHaveLength(0)
  })
})

describe("testConnection", () => {
  it("stores the API base that answered", async () => {
    const { handle, area } = setup(
      { "https://h/api/extension/me": jsonResponse(200, IDENTITY) },
      { serverUrl: "https://h", apiBase: "", token: TOKEN }
    )
    expect(await handle({ type: "testConnection" })).toEqual({
      tone: "success",
      message: 'Connected as kim · token "laptop" · server v0.3.0',
    })
    expect(area.data.get("apiBase")).toBe(API)
  })

  it("clears the API base when the test fails", async () => {
    const { handle, area } = setup({
      "https://h/api/extension/me": jsonResponse(401, { detail: "nope" }),
    })
    expect((await handle({ type: "testConnection" })).tone).toBe("error")
    expect(area.data.get("apiBase")).toBe("")
  })
})

describe("listTasks", () => {
  const LIST = { type: "listTasks" } as const
  const ROW = {
    task_id: "t1",
    task_type: "DOWNLOAD",
    status: "IN_PROGRESS",
    title: "A video",
    percent_complete: 42,
  }

  it("gets the task list with the token, and no cookie", async () => {
    const { handle, calls, hasPermission } = setup({
      [`${API}/extension/tasks`]: jsonResponse(200, { tasks: [ROW], has_more: false }),
    })
    const feed = await handle(LIST)
    expect(feed).toMatchObject({ ok: true, hasMore: false })
    expect(feed.ok && feed.tasks.map((task) => task.id)).toEqual(["t1"])
    expect(hasPermission).toHaveBeenCalledWith("https://h/*")
    expect(calls).toHaveLength(1)
    expect(calls[0].init.method).toBe("GET")
    expect(calls[0].init.credentials).toBe("omit")
    expect((calls[0].init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`)
    expect(calls[0].init.body).toBeUndefined()
  })

  it("tells the caller to stop when setup is missing", async () => {
    const { handle, calls } = setup({}, {})
    expect(await handle(LIST)).toEqual({
      ok: false,
      message: SETUP_MESSAGE,
      fatal: true,
      retryAfterSeconds: null,
    })
    expect(calls).toHaveLength(0)
  })

  it("tells the caller to stop when the host permission is missing", async () => {
    const { handle, calls } = setup({}, undefined, false)
    expect(await handle(LIST)).toMatchObject({ ok: false, fatal: true })
    expect(calls).toHaveLength(0)
  })

  it("keeps polling through a network failure", async () => {
    const { handle } = setup({ [`${API}/extension/tasks`]: new TypeError("Failed to fetch") })
    expect(await handle(LIST)).toMatchObject({ ok: false, fatal: false })
  })

  it("stops polling once the token is refused", async () => {
    const { handle } = setup({
      [`${API}/extension/tasks`]: jsonResponse(401, { detail: "A valid browser-extension token is required." }),
    })
    expect(await handle(LIST)).toMatchObject({ ok: false, fatal: true })
  })
})

describe("the token never leaks", () => {
  const failures = {
    "401": jsonResponse(401, { detail: "A valid browser-extension token is required." }),
    "403": jsonResponse(403, { detail: { message: "Storage limit reached", storage_used_bytes: 1, storage_limit_bytes: 1 } }),
    "409": jsonResponse(409, { detail: { message: "Download task already exists", task_id: "t" } }),
    "422": jsonResponse(422, { detail: [{ msg: `bad ${TOKEN}`, loc: ["header"] }] }),
    "429": jsonResponse(429, { detail: "Too many requests." }, { "retry-after": "5" }),
    "500": jsonResponse(500, { detail: "boom" }),
    html: htmlResponse(200),
    redirect: () => redirectResponse(),
    network: new TypeError(`Failed to fetch with ${TOKEN}`),
    timeout: new DOMException(`timed out ${TOKEN}`, "TimeoutError"),
  }

  it.each(Object.entries(failures))("%s", async (_name, failure) => {
    const consoleSpies = (["log", "info", "warn", "error", "debug"] as const).map((method) =>
      vi.spyOn(console, method).mockImplementation(() => undefined)
    )
    const { handle } = setup({
      [`${API}/ytdl/`]: failure,
      [`${API}/subscriptions`]: failure,
      [`${API}/extension/tasks`]: failure,
      "https://h/api/extension/me": failure,
      "https://h/extension/me": failure,
    })

    for (const message of [DOWNLOAD, SUBSCRIBE, { type: "testConnection" } as const]) {
      const outcome = await handle(message)
      expect(outcome.tone).toBe("error")
      expect(JSON.stringify(outcome)).not.toContain(TOKEN)
      expect(JSON.stringify(outcome)).not.toContain(TOKEN.slice(6))
    }
    const feed = await handle({ type: "listTasks" })
    expect(feed.ok).toBe(false)
    expect(JSON.stringify(feed)).not.toContain(TOKEN)
    expect(JSON.stringify(feed)).not.toContain(TOKEN.slice(6))
    for (const spy of consoleSpies) {
      for (const args of spy.mock.calls) expect(JSON.stringify(args)).not.toContain(TOKEN)
    }
  })
})
