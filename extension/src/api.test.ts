import { describe, expect, it } from "vitest"
import {
  apiBaseCandidates,
  describeDownload,
  describeError,
  describeFailure,
  describeIdentity,
  describeSubscription,
  describeTasks,
  downloadBody,
  formatBytes,
  isPlainHttp,
  MAX_TASK_ROWS,
  MIN_SERVER_VERSION,
  normalizeServerUrl,
  originPattern,
  parseServerTime,
  REJECTED_TOKEN_MESSAGE,
  resolveConnection,
  retryAfterSeconds,
  sendRequest,
  subscribeBody,
} from "./api"
import {
  DEFAULT_OPTIONS,
  htmlResponse,
  jsonResponse,
  NO_FILTERS,
  redirectResponse,
  reply,
  routedFetch,
  TOKEN,
} from "./testing"

const CANCELLED_RETRY_MESSAGE =
  "A cancelled download for this video still holds its place in the queue. Retry that task from " +
  "the Tasks tab, or delete it and submit again."

describe("request bodies", () => {
  it.each([
    ["VIDEO", false, false, false],
    ["VIDEO", true, false, true],
    ["AUDIO", false, true, false],
    ["AUDIO", true, true, true],
    ["TRANSCRIPT", false, true, true],
  ] as const)("download as %s (transcribe=%s)", (mediaType, transcribe, audioOnly, generate) => {
    expect(
      downloadBody({
        ...DEFAULT_OPTIONS,
        url: "https://www.youtube.com/watch?v=x",
        mediaType,
        transcribe,
        overwrite: false,
        wholePlaylist: false,
      })
    ).toEqual({
      url: "https://www.youtube.com/watch?v=x",
      media_type: mediaType,
      audio_only: audioOnly,
      generate_transcript: generate,
      download_quality: "BEST",
      audio_quality: "BEST",
      overwrite: false,
      download_playlist: false,
    })
  })

  it("a download carries its quality, overwrite and playlist choices", () => {
    const body = downloadBody({
      ...DEFAULT_OPTIONS,
      url: "https://www.youtube.com/playlist?list=PL1",
      videoQuality: "720P",
      audioQuality: "96K",
      overwrite: true,
      wholePlaylist: true,
    })
    expect(body).toMatchObject({
      download_quality: "720P",
      audio_quality: "96K",
      overwrite: true,
      download_playlist: true,
    })
  })

  it("a subscription with no filters sends nulls for them, and never overwrite", () => {
    expect(
      subscribeBody({ ...DEFAULT_OPTIONS, ...NO_FILTERS, url: "https://www.youtube.com/@h", mediaType: "AUDIO" })
    ).toEqual({
      url: "https://www.youtube.com/@h",
      media_type: "AUDIO",
      audio_only: true,
      generate_transcript: false,
      download_quality: "BEST",
      audio_quality: "BEST",
      string_match: null,
      date_filter: null,
      min_duration_seconds: null,
      max_duration_seconds: null,
      enabled: true,
    })
  })

  it("a subscription sends its filters, with the title filter trimmed", () => {
    const body = subscribeBody({
      ...DEFAULT_OPTIONS,
      url: "https://www.youtube.com/@h",
      titleFilter: "  live  ",
      uploadedAfter: "2026-01-15T05:00:00.000Z",
      minDurationSeconds: 90,
      maxDurationSeconds: 3600,
    })
    expect(body).toMatchObject({
      string_match: "live",
      date_filter: "2026-01-15T05:00:00.000Z",
      min_duration_seconds: 90,
      max_duration_seconds: 3600,
    })
  })

  it("a transcript-only subscription asks for the transcript", () => {
    const body = subscribeBody({
      ...DEFAULT_OPTIONS,
      ...NO_FILTERS,
      url: "https://www.youtube.com/@h",
      mediaType: "TRANSCRIPT",
    })
    expect(body).toMatchObject({ media_type: "TRANSCRIPT", audio_only: true, generate_transcript: true })
  })
})

describe("sendRequest", () => {
  it("sends the token as a bearer header, never a cookie, and doesn't follow redirects", async () => {
    const { fetchImpl, calls } = routedFetch({
      "https://h/api/ytdl/": jsonResponse(201, { task_id: "t1" }),
    })
    const result = await sendRequest(fetchImpl, "POST", "https://h/api/ytdl/", TOKEN, { url: "u" })

    expect(result).toEqual(reply(201, { task_id: "t1" }))
    const { init } = calls[0]
    expect(init.method).toBe("POST")
    expect(init.headers).toEqual({
      Accept: "application/json",
      Authorization: `Bearer ${TOKEN}`,
      "Content-Type": "application/json",
    })
    expect(init.credentials).toBe("omit")
    expect(init.redirect).toBe("manual")
    expect(init.body).toBe(JSON.stringify({ url: "u" }))
    expect(init.signal).toBeInstanceOf(AbortSignal)
  })

  it("sends no Authorization header without a token", async () => {
    const { fetchImpl, calls } = routedFetch({ "https://h/v": jsonResponse(200, {}) })
    await sendRequest(fetchImpl, "GET", "https://h/v", null)
    expect(calls[0].init.headers).toEqual({ Accept: "application/json" })
    expect(calls[0].init.body).toBeUndefined()
  })

  it("marks HTML and redirects", async () => {
    const { fetchImpl } = routedFetch({
      "https://h/html": htmlResponse(200),
      "https://h/redirect": () => redirectResponse(),
      "https://h/limited": jsonResponse(429, { detail: "Too many" }, { "retry-after": "12" }),
    })
    expect(await sendRequest(fetchImpl, "GET", "https://h/html", TOKEN)).toMatchObject({
      status: 200,
      json: false,
    })
    expect(await sendRequest(fetchImpl, "GET", "https://h/redirect", TOKEN)).toMatchObject({
      redirected: true,
      json: false,
    })
    expect(await sendRequest(fetchImpl, "GET", "https://h/limited", TOKEN)).toMatchObject({
      status: 429,
      retryAfter: "12",
    })
  })
})

describe("describeError", () => {
  it("401 explains every way a token stops working", () => {
    const message = describeError(reply(401, { detail: "A valid browser-extension token is required." }))
    expect(message).toBe(REJECTED_TOKEN_MESSAGE)
    expect(message).toMatch(/revoked/)
    expect(message).toMatch(/expired/)
    expect(message).toMatch(/password change/)
    expect(message).toMatch(/MCP token/)
    expect(message).toMatch(/settings/)
  })

  it("shows the server's 409 messages verbatim", () => {
    for (const message of [
      CANCELLED_RETRY_MESSAGE,
      "Download task already exists",
      "Set overwrite to re-download media. Current status: COMPLETE",
      'This video was previously deleted. Enable "Overwrite" to re-download it.',
    ]) {
      expect(describeError(reply(409, { detail: { message, task_id: "t" } }))).toBe(message)
    }
  })

  it("spells out a storage limit", () => {
    const body = {
      detail: {
        message: "Storage limit reached",
        storage_used_bytes: 10 * 1024 ** 3,
        storage_limit_bytes: 10 * 1024 ** 3,
      },
    }
    expect(describeError(reply(403, body))).toBe(
      "Storage limit reached: 10 GB of 10 GB used. Transcript-only downloads don't count toward it."
    )
  })

  it("passes other 403 details through", () => {
    expect(describeError(reply(403, { detail: "Account pending admin approval" }))).toBe(
      "Account pending admin approval"
    )
    expect(describeError(reply(403, {}))).toBe("The server refused the request.")
  })

  it("422 shows the first validation message", () => {
    const body = { detail: [{ loc: ["body", "url"], msg: "Field required" }, { msg: "second" }] }
    expect(describeError(reply(422, body))).toBe("The server rejected the request: Field required.")
  })

  it("429 honours Retry-After", () => {
    expect(describeError(reply(429, { detail: "x" }, { retryAfter: "30" }))).toBe(
      "Too many requests. Try again in 30 seconds."
    )
    expect(describeError(reply(429, { detail: "x" }, { retryAfter: "1" }))).toBe(
      "Too many requests. Try again in 1 second."
    )
    expect(describeError(reply(429, null, { retryAfter: null, json: false }))).toBe(
      "Too many requests. Try again in a minute."
    )
  })

  it("404 names the minimum server version", () => {
    expect(describeError(reply(404, { detail: "Not Found" }))).toContain(MIN_SERVER_VERSION)
  })

  it("5xx, non-JSON and redirects each get their own message", () => {
    expect(describeError(reply(502, null, { json: false }))).toBe(
      "The server hit an error (HTTP 502). Try again later."
    )
    expect(describeError(reply(401, null, { json: false }))).toMatch(/didn't answer like YT-Hoarder \(HTTP 401\)/)
    expect(describeError(reply(0, null, { json: false, redirected: true }))).toMatch(/redirected/)
  })

  it("falls back to the detail, then to the status", () => {
    expect(describeError(reply(400, { detail: "Bad thing" }))).toBe("Bad thing")
    expect(describeError(reply(418, {}))).toBe("Unexpected response from the server (HTTP 418).")
  })
})

describe("describeDownload / describeSubscription", () => {
  it("a queued download", () => {
    expect(describeDownload(reply(201, { task_id: "abc" }))).toEqual({
      tone: "success",
      message: "Queued. Follow it in YT-Hoarder's Tasks tab.",
    })
  })

  it("access granted to someone else's download", () => {
    expect(
      describeDownload(reply(201, { message: "Media already exists. Access granted.", media_details_id: 4 }))
    ).toEqual({ tone: "success", message: "Media already exists. Access granted." })
  })

  it("a conflicting download", () => {
    expect(describeDownload(reply(409, { detail: { message: CANCELLED_RETRY_MESSAGE } }))).toEqual({
      tone: "error",
      message: CANCELLED_RETRY_MESSAGE,
    })
  })

  it("a duplicate subscription is information, not an error", () => {
    expect(describeSubscription(reply(201, { task: "DUPLICATE_SUBSCRIPTION" }))).toEqual({
      tone: "info",
      message: "You're already subscribed to this.",
    })
  })

  it("a new subscription", () => {
    expect(describeSubscription(reply(201, { task: "sub-task" })).tone).toBe("success")
  })

  it("a 2xx that isn't JSON is an error", () => {
    expect(describeSubscription(reply(200, null, { json: false })).tone).toBe("error")
  })
})

describe("describeFailure", () => {
  it("a timeout", () => {
    const error = new DOMException("signal timed out", "TimeoutError")
    expect(describeFailure(error, "https://h")).toBe("https://h didn't answer within 15 seconds.")
  })

  it("a network error never echoes the error text", () => {
    const message = describeFailure(new TypeError(`fetch failed ${TOKEN}`), "https://h")
    expect(message).toBe("Couldn't reach https://h. Check the server URL and that the server is running.")
  })
})

describe("server URLs", () => {
  it("normalizes", () => {
    expect(normalizeServerUrl(" https://Hoarder.example.com/ ")).toEqual({
      ok: true,
      url: "https://hoarder.example.com",
    })
    expect(normalizeServerUrl("http://localhost:8000//?x=1#y")).toEqual({
      ok: true,
      url: "http://localhost:8000",
    })
    expect(normalizeServerUrl("https://h.example.com/api/")).toEqual({
      ok: true,
      url: "https://h.example.com/api",
    })
  })

  it("requires a scheme", () => {
    expect(normalizeServerUrl("hoarder.local:8000")).toMatchObject({ ok: false })
    expect(normalizeServerUrl("ftp://h")).toMatchObject({ ok: false })
    expect(normalizeServerUrl("https://")).toMatchObject({ ok: false })
  })

  it("asks for host permission without a port", () => {
    expect(originPattern("http://localhost:8000/api")).toBe("http://localhost/*")
    expect(originPattern("https://hoarder.example.com")).toBe("https://hoarder.example.com/*")
  })

  it("flags plain HTTP", () => {
    expect(isPlainHttp("http://nas:8000")).toBe(true)
    expect(isPlainHttp("https://nas")).toBe(false)
  })

  it("tries /api first unless the URL already ends with it", () => {
    expect(apiBaseCandidates("https://h")).toEqual(["https://h/api", "https://h"])
    expect(apiBaseCandidates("https://h/api")).toEqual(["https://h/api", "https://h/api/api"])
  })

  it("adds :8000 of the same host for the dev web app's :3000", () => {
    expect(apiBaseCandidates("http://localhost:3000")).toEqual([
      "http://localhost:3000/api",
      "http://localhost:3000",
      "http://localhost:8000",
    ])
    expect(apiBaseCandidates("http://192.168.1.50:3000").at(-1)).toBe("http://192.168.1.50:8000")
    expect(apiBaseCandidates("http://localhost:8000")).toHaveLength(2)
  })

  it("the permission for the web app's address also covers the dev API's", () => {
    expect(originPattern("http://localhost:8000")).toBe(originPattern("http://localhost:3000"))
  })
})

describe("resolveConnection", () => {
  const identity = {
    username: "kim",
    token_name: "laptop",
    token_prefix: "ytdlh_abcdef",
    expires_at: null,
    app_version: "0.3.0",
  }

  it("finds the bundled app's API under /api", async () => {
    const { fetchImpl, calls } = routedFetch({
      "https://h/api/extension/me": jsonResponse(200, identity),
    })
    expect(await resolveConnection(fetchImpl, "https://h", TOKEN)).toEqual({
      ok: true,
      apiBase: "https://h/api",
      identity,
    })
    expect(calls[0].init.headers).toMatchObject({ Authorization: `Bearer ${TOKEN}` })
  })

  it("finds a dev backend at the root", async () => {
    const { fetchImpl } = routedFetch({ "http://localhost:8000/extension/me": jsonResponse(200, identity) })
    expect(await resolveConnection(fetchImpl, "http://localhost:8000", TOKEN)).toMatchObject({
      ok: true,
      apiBase: "http://localhost:8000",
    })
  })

  it("finds the dev API on :8000 when given the dev web app's :3000", async () => {
    const { fetchImpl, calls } = routedFetch({
      "http://localhost:3000/api/extension/me": () => redirectResponse(),
      "http://localhost:3000/extension/me": () => redirectResponse(),
      "http://localhost:8000/extension/me": jsonResponse(200, identity),
    })
    expect(await resolveConnection(fetchImpl, "http://localhost:3000", TOKEN)).toEqual({
      ok: true,
      apiBase: "http://localhost:8000",
      identity,
    })
    expect(calls.map((call) => call.url)).toEqual([
      "http://localhost:3000/api/extension/me",
      "http://localhost:3000/extension/me",
      "http://localhost:8000/extension/me",
    ])
  })

  it("accepts the API URL itself", async () => {
    const { fetchImpl } = routedFetch({ "https://h/api/extension/me": jsonResponse(200, identity) })
    expect(await resolveConnection(fetchImpl, "https://h/api", TOKEN)).toMatchObject({
      ok: true,
      apiBase: "https://h/api",
    })
  })

  it("skips the web app's HTML shell", async () => {
    const { fetchImpl } = routedFetch({
      "https://h/sub/api/extension/me": htmlResponse(200),
      "https://h/sub/extension/me": jsonResponse(200, identity),
    })
    expect(await resolveConnection(fetchImpl, "https://h/sub", TOKEN)).toMatchObject({
      ok: true,
      apiBase: "https://h/sub",
    })
  })

  it("names an older server's version", async () => {
    const { fetchImpl, calls } = routedFetch({
      "https://h/extension/me": htmlResponse(200),
      "https://h/api/ytdl/version": jsonResponse(200, { app_version: "0.2.0", ytdlp_version: "x" }),
    })
    const result = await resolveConnection(fetchImpl, "https://h", TOKEN)
    expect(result).toEqual({
      ok: false,
      message:
        "This server runs YT-Hoarder 0.2.0, which doesn't accept browser-extension tokens. Update it to 0.3.0 or later.",
    })
    const versionCall = calls.find((call) => call.url.endsWith("/ytdl/version"))
    expect(versionCall?.init.headers).not.toHaveProperty("Authorization")
  })

  it("reports a rejected token", async () => {
    const { fetchImpl } = routedFetch({
      "https://h/api/extension/me": jsonResponse(401, { detail: "A valid browser-extension token is required." }),
    })
    expect(await resolveConnection(fetchImpl, "https://h", TOKEN)).toEqual({
      ok: false,
      message: REJECTED_TOKEN_MESSAGE,
    })
  })

  it("reports an unreachable server", async () => {
    const { fetchImpl } = routedFetch({ "https://h/api/extension/me": new TypeError("fetch failed") })
    expect(await resolveConnection(fetchImpl, "https://h", TOKEN)).toMatchObject({
      ok: false,
      message: expect.stringMatching(/^Couldn't reach https:\/\/h\./),
    })
  })

  it("reports a non-YT-Hoarder server", async () => {
    const { fetchImpl } = routedFetch({
      "https://h/api/extension/me": htmlResponse(200),
      "https://h/extension/me": htmlResponse(200),
    })
    expect(await resolveConnection(fetchImpl, "https://h", TOKEN)).toEqual({
      ok: false,
      message: "No YT-Hoarder server found at https://h.",
    })
  })

  it("reports a proxy's sign-in page", async () => {
    const { fetchImpl } = routedFetch({
      "https://h/api/extension/me": htmlResponse(401),
      "https://h/extension/me": htmlResponse(401),
    })
    const result = await resolveConnection(fetchImpl, "https://h", TOKEN)
    expect(result).toMatchObject({ ok: false, message: expect.stringMatching(/sign-in proxy/) })
  })
})

describe("describeIdentity", () => {
  const identity = {
    username: "kim",
    token_name: "laptop",
    token_prefix: "ytdlh_abcdef",
    expires_at: null,
    app_version: "0.3.0",
  }

  it("names the user, token and server version", () => {
    expect(describeIdentity(identity)).toBe('Connected as kim · token "laptop" · server v0.3.0')
  })

  it("adds the expiry when there is one", () => {
    expect(describeIdentity({ ...identity, expires_at: "2027-01-31T12:00:00" })).toMatch(
      /^Connected as kim · token "laptop" · server v0\.3\.0 · expires .+$/
    )
  })
})

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [512, "512 B"],
    [1536, "1.5 KB"],
    [10 * 1024 ** 2, "10 MB"],
    [9.5 * 1024 ** 3, "9.5 GB"],
  ])("%d → %s", (bytes, text) => {
    expect(formatBytes(bytes)).toBe(text)
  })
})

describe("parseServerTime", () => {
  it.each([
    ["2026-01-02T03:04:05Z", Date.UTC(2026, 0, 2, 3, 4, 5)],
    ["2026-01-02T03:04:05+00:00", Date.UTC(2026, 0, 2, 3, 4, 5)],
    ["2026-01-02T05:04:05+02:00", Date.UTC(2026, 0, 2, 3, 4, 5)],
    // Naive timestamps are UTC, whatever zone this machine is in.
    ["2026-01-02T03:04:05", Date.UTC(2026, 0, 2, 3, 4, 5)],
    ["2026-01-02T03:04:05.250000", Date.UTC(2026, 0, 2, 3, 4, 5, 250)],
  ])("%s", (text, expected) => {
    expect(parseServerTime(text)).toBe(expected)
  })

  it("rejects what isn't a timestamp", () => {
    expect(parseServerTime("soon")).toBeNull()
  })
})

describe("retryAfterSeconds", () => {
  it.each([
    ["5", 5],
    ["120", 120],
    ["0", null],
    ["-3", null],
    ["soon", null],
    [null, null],
  ])("%s → %s", (header, expected) => {
    expect(retryAfterSeconds(reply(429, {}, { retryAfter: header }))).toBe(expected)
  })
})

describe("describeTasks", () => {
  const row = (overrides: Record<string, unknown> = {}) => ({
    task_id: "t1",
    task_type: "DOWNLOAD",
    status: "IN_PROGRESS",
    title: "A video",
    channel: "A channel",
    media_type: "VIDEO",
    percent_complete: 42,
    eta_seconds: 90.5,
    status_message: "Downloading",
    download_phase: "VIDEO",
    retry_count: 0,
    next_retry_at: null,
    sleep_until: null,
    error_code: null,
    updated_at: "2026-01-02T03:04:05+00:00",
    ...overrides,
  })
  const feed = (tasks: unknown[], extra: Record<string, unknown> = {}) =>
    describeTasks(reply(200, { tasks, has_more: false, ...extra }))
  const rows = (tasks: unknown[]) => {
    const result = feed(tasks)
    if (!result.ok) throw new Error(result.message)
    return result.tasks
  }

  it("turns a task row into a view", () => {
    expect(feed([row()])).toEqual({
      ok: true,
      hasMore: false,
      tasks: [
        {
          id: "t1",
          title: "A video",
          channel: "A channel",
          kind: "download",
          status: "IN_PROGRESS",
          percent: 42,
          etaSeconds: 90.5,
          phase: "VIDEO",
          message: "Downloading",
          errorCode: null,
          retryAtMs: null,
          sleepUntilMs: null,
        },
      ],
    })
  })

  it("reads the retry and sleep deadlines as UTC times", () => {
    const [task] = rows([
      row({ next_retry_at: "2026-01-02T03:04:05", sleep_until: "2026-01-02T03:04:05+00:00" }),
    ])
    expect(task.retryAtMs).toBe(Date.UTC(2026, 0, 2, 3, 4, 5))
    expect(task.sleepUntilMs).toBe(Date.UTC(2026, 0, 2, 3, 4, 5))
  })

  it.each([
    ["DOWNLOAD", "download"],
    ["TRANSCRIPT_GENERATION", "transcript"],
    ["CLIP_GENERATION", "clip"],
    ["MEDIA_CONVERSION", "conversion"],
  ])("labels %s as %s", (taskType, kind) => {
    expect(rows([row({ task_type: taskType })])[0].kind).toBe(kind)
  })

  it("clamps and rounds the percentage, and drops numbers that aren't", () => {
    expect(rows([row({ percent_complete: 150 })])[0].percent).toBe(100)
    expect(rows([row({ percent_complete: -4 })])[0].percent).toBe(0)
    expect(rows([row({ percent_complete: 41.6 })])[0].percent).toBe(42)
    expect(rows([row({ percent_complete: "42" })])[0].percent).toBeNull()
    expect(rows([row({ percent_complete: null })])[0].percent).toBeNull()
  })

  it("ignores an eta that can't be right and a phase it doesn't know", () => {
    const [task] = rows([row({ eta_seconds: -1, download_phase: "WARP" })])
    expect(task.etaSeconds).toBeNull()
    expect(task.phase).toBeNull()
  })

  it("drops rows it can't use and keeps the rest", () => {
    const kept = rows([
      row({ task_id: "a" }),
      "nonsense",
      null,
      row({ task_id: "" }),
      row({ task_id: "b", status: "TELEPORTING" }),
      row({ task_id: "c", task_type: "SPRITE_GENERATION" }),
      row({ task_id: "d", status: undefined }),
      row({ task_id: "e", status: "COMPLETE" }),
    ])
    expect(kept.map((task) => task.id)).toEqual(["a", "e"])
  })

  it("shows at most MAX_TASK_ROWS and says there are more", () => {
    const many = Array.from({ length: MAX_TASK_ROWS + 5 }, (_, n) => row({ task_id: `t${n}` }))
    const result = feed(many)
    expect(result.ok && result.tasks).toHaveLength(MAX_TASK_ROWS)
    expect(result.ok && result.hasMore).toBe(true)
  })

  it("passes the server's own has_more flag on", () => {
    const result = feed([row()], { has_more: true })
    expect(result.ok && result.hasMore).toBe(true)
  })

  it("cuts a token out of any text and caps how long text can be", () => {
    const [task] = rows([
      row({ title: `leaked ${TOKEN}`, status_message: "x".repeat(1000), error_code: `code ${TOKEN}` }),
    ])
    expect(JSON.stringify(task)).not.toContain(TOKEN)
    expect(JSON.stringify(task)).not.toContain(TOKEN.slice(6))
    expect(task.message?.length).toBeLessThan(400)
  })

  it("gives an empty list", () => {
    expect(feed([])).toEqual({ ok: true, tasks: [], hasMore: false })
  })

  it("is fatal when the body isn't a task list", () => {
    const result = describeTasks(reply(200, { tasks: "no" }))
    expect(result).toMatchObject({ ok: false, fatal: true })
    expect(describeTasks(reply(200, { detail: "hello" }))).toMatchObject({ ok: false, fatal: true })
  })

  describe("failures", () => {
    it("a refused token is fatal, so polling stops", () => {
      expect(describeTasks(reply(401, { detail: "nope" }))).toEqual({
        ok: false,
        message: REJECTED_TOKEN_MESSAGE,
        fatal: true,
        retryAfterSeconds: null,
      })
    })

    it("a server too old for the route says so", () => {
      const result = describeTasks(reply(404, { detail: "Not Found" }))
      expect(result).toMatchObject({ ok: false, fatal: true })
      expect(!result.ok && result.message).toContain("too old to list tasks")
    })

    it.each([
      ["403", reply(403, { detail: "no" })],
      ["a redirect", reply(0, null, { redirected: true, json: false })],
      ["a page that isn't JSON", reply(200, undefined, { json: false })],
    ])("%s is fatal", (_name, failure) => {
      expect(describeTasks(failure)).toMatchObject({ ok: false, fatal: true })
    })

    it("a rate limit is not fatal, and carries how long to wait", () => {
      expect(describeTasks(reply(429, { detail: "slow" }, { retryAfter: "12" }))).toMatchObject({
        ok: false,
        fatal: false,
        retryAfterSeconds: 12,
      })
    })

    it("a server error is not fatal", () => {
      expect(describeTasks(reply(503, { detail: "down" }))).toMatchObject({
        ok: false,
        fatal: false,
        retryAfterSeconds: null,
      })
    })
  })
})
