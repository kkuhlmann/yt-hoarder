/**
 * Talking to a YT-Hoarder server: request shapes, finding the API under a user-entered URL, and
 * turning every reply into one sentence for the popup or settings page.
 *
 * Nothing here may put the token into a message: replies and errors are described from status
 * codes and the server's own `detail`, never from the request that produced them.
 */

import type { AudioQuality, VideoQuality } from "./choices"

export const TOKEN_PREFIX = "ytdlh_"
export const MIN_SERVER_VERSION = "0.3.0"
export const REQUEST_TIMEOUT_MS = 15_000

export type MediaChoice = "VIDEO" | "AUDIO" | "TRANSCRIPT"

/** What a download and a subscription choose alike. */
export type MediaOptions = {
  mediaType: MediaChoice
  transcribe: boolean
  videoQuality: VideoQuality
  audioQuality: AudioQuality
}

/**
 * Only a download offers overwrite. On a subscription it would replace every video of the channel
 * already kept, on every check, so the server refuses it from an extension token.
 */
export type DownloadRequest = MediaOptions & {
  url: string
  wholePlaylist: boolean
  overwrite: boolean
}

export type SubscribeRequest = MediaOptions & {
  url: string
  titleFilter: string
  /** ISO timestamp; only uploads released after it are downloaded. */
  uploadedAfter: string | null
  minDurationSeconds: number | null
  maxDurationSeconds: number | null
}

export type Identity = {
  username: string
  token_name: string
  token_prefix: string
  expires_at: string | null
  app_version: string
}

export type Tone = "success" | "info" | "error"
export type Outcome = { tone: Tone; message: string }

export type HttpReply = {
  status: number
  /** The server answered with a redirect, which is reported rather than followed. */
  redirected: boolean
  retryAfter: string | null
  /** True only when the reply was JSON and parsed. */
  json: boolean
  body: unknown
}

export type Connection =
  | { ok: true; apiBase: string; identity: Identity }
  | { ok: false; message: string }

type FetchLike = (input: string, init: RequestInit) => Promise<Response>

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function mediaFields(options: MediaOptions): Record<string, unknown> {
  return {
    media_type: options.mediaType,
    audio_only: options.mediaType !== "VIDEO",
    generate_transcript: options.mediaType === "TRANSCRIPT" || options.transcribe,
    download_quality: options.videoQuality,
    audio_quality: options.audioQuality,
  }
}

export function downloadBody(request: DownloadRequest): Record<string, unknown> {
  return {
    url: request.url,
    ...mediaFields(request),
    overwrite: request.overwrite,
    download_playlist: request.wholePlaylist,
  }
}

export function subscribeBody(request: SubscribeRequest): Record<string, unknown> {
  return {
    url: request.url,
    ...mediaFields(request),
    string_match: request.titleFilter.trim() || null,
    date_filter: request.uploadedAfter,
    min_duration_seconds: request.minDurationSeconds,
    max_duration_seconds: request.maxDurationSeconds,
    enabled: true,
  }
}

export async function sendRequest(
  fetchImpl: FetchLike,
  method: "GET" | "POST",
  url: string,
  token: string | null,
  body?: unknown
): Promise<HttpReply> {
  const headers: Record<string, string> = { Accept: "application/json" }
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers["Content-Type"] = "application/json"

  const response = await fetchImpl(url, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    // The token is the only credential. An attached session cookie would let a request the
    // server can't tie to a token fall back to whoever is signed in to the web app.
    credentials: "omit",
    // A redirect to another origin (http → https) drops the Authorization header, so following it
    // would turn a wrong URL into a confusing 401.
    redirect: "manual",
    cache: "no-store",
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  })

  const isJson = (response.headers.get("content-type") ?? "").includes("json")
  let parsed: unknown = undefined
  if (isJson) {
    try {
      parsed = await response.json()
    } catch {
      parsed = undefined
    }
  }
  return {
    status: response.status,
    redirected: response.type === "opaqueredirect",
    retryAfter: response.headers.get("retry-after"),
    json: isJson && parsed !== undefined,
    body: parsed,
  }
}

export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB", "PB"]
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  const shown = unit === 0 || value >= 10 ? Math.round(value).toString() : value.toFixed(1)
  return `${shown} ${units[unit]}`
}

function redactTokens(text: string): string {
  return text.replace(/ytdlh_[\w-]+/g, `${TOKEN_PREFIX}…`)
}

function detailMessage(body: unknown): string | null {
  if (!isRecord(body)) return null
  const { detail } = body
  let message: unknown = null
  if (typeof detail === "string") message = detail
  else if (isRecord(detail)) message = detail.message
  else if (Array.isArray(detail)) message = detail.find(isRecord)?.msg
  // Server text is shown verbatim, so a token echoed back by anything in between is cut out.
  return typeof message === "string" ? redactTokens(message) : null
}

function storageLimitMessage(body: unknown): string | null {
  if (!isRecord(body) || !isRecord(body.detail)) return null
  const { message, storage_used_bytes: used, storage_limit_bytes: limit } = body.detail
  if (message !== "Storage limit reached" || typeof used !== "number" || typeof limit !== "number") {
    return null
  }
  return (
    `Storage limit reached: ${formatBytes(used)} of ${formatBytes(limit)} used. ` +
    "Transcript-only downloads don't count toward it."
  )
}

export const REJECTED_TOKEN_MESSAGE =
  "The server rejected the token. It may have been revoked or expired, a password change may " +
  "have invalidated it, or it may be an MCP token rather than a browser-extension token. " +
  "Check it in the extension's settings."

const REDIRECT_MESSAGE =
  "The server redirected the request. Enter the address it redirects to in the extension's " +
  "settings, for example https:// instead of http://."

const NOT_FOUND_MESSAGE =
  `No YT-Hoarder API answered at this address, or the server is older than ${MIN_SERVER_VERSION}. ` +
  "Check the server URL in the extension's settings."

/** The wait a 429 asks for, in whole seconds, or null when it names none. */
export function retryAfterSeconds(reply: HttpReply): number | null {
  const seconds = Number.parseInt(reply.retryAfter ?? "", 10)
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null
}

/** One sentence for any reply that isn't the success the caller hoped for. */
export function describeError(reply: HttpReply): string {
  if (reply.redirected) return REDIRECT_MESSAGE
  if (reply.status === 429) {
    const seconds = retryAfterSeconds(reply)
    return seconds !== null
      ? `Too many requests. Try again in ${seconds} second${seconds === 1 ? "" : "s"}.`
      : "Too many requests. Try again in a minute."
  }
  if (reply.status >= 500) {
    return `The server hit an error (HTTP ${reply.status}). Try again later.`
  }
  if (!reply.json) {
    return (
      `The server didn't answer like YT-Hoarder (HTTP ${reply.status}). If a sign-in proxy ` +
      "protects it, let the extension's requests through."
    )
  }
  const detail = detailMessage(reply.body)
  switch (reply.status) {
    case 401:
      return REJECTED_TOKEN_MESSAGE
    case 403:
      return storageLimitMessage(reply.body) ?? detail ?? "The server refused the request."
    case 404:
      return NOT_FOUND_MESSAGE
    case 422:
      return `The server rejected the request: ${detail ?? "invalid input"}.`
    default:
      return detail ?? `Unexpected response from the server (HTTP ${reply.status}).`
  }
}

function isSuccess(reply: HttpReply): boolean {
  return reply.status >= 200 && reply.status < 300 && reply.json && isRecord(reply.body)
}

export function describeDownload(reply: HttpReply): Outcome {
  if (!isSuccess(reply)) return { tone: "error", message: describeError(reply) }
  const body = reply.body as Record<string, unknown>
  if (typeof body.message === "string") return { tone: "success", message: redactTokens(body.message) }
  return { tone: "success", message: "Queued. Follow it in YT-Hoarder's Tasks tab." }
}

export function describeSubscription(reply: HttpReply): Outcome {
  if (!isSuccess(reply)) return { tone: "error", message: describeError(reply) }
  const body = reply.body as Record<string, unknown>
  if (body.task === "DUPLICATE_SUBSCRIPTION") {
    return { tone: "info", message: "You're already subscribed to this." }
  }
  return {
    tone: "success",
    message: "Subscription added. It appears in Subscriptions once YT-Hoarder has looked it up.",
  }
}

export function describeFailure(error: unknown, origin: string): string {
  const name = isRecord(error) ? error.name : undefined
  if (name === "TimeoutError" || name === "AbortError") {
    return `${origin} didn't answer within ${REQUEST_TIMEOUT_MS / 1000} seconds.`
  }
  return `Couldn't reach ${origin}. Check the server URL and that the server is running.`
}

export type ServerUrl = { ok: true; url: string } | { ok: false; message: string }

/** The user's server address without query, hash or trailing slash, or why it can't be used. */
export function normalizeServerUrl(input: string): ServerUrl {
  const trimmed = input.trim()
  if (!/^https?:\/\//i.test(trimmed)) {
    return { ok: false, message: "Enter the full address, starting with https:// or http://." }
  }
  let parsed: URL
  try {
    parsed = new URL(trimmed)
  } catch {
    return { ok: false, message: "That isn't a valid address." }
  }
  return { ok: true, url: `${parsed.origin}${parsed.pathname.replace(/\/+$/, "")}` }
}

/**
 * The host permission pattern for a server. Without a port: Firefox rejects patterns that carry
 * one, and a port-less pattern matches every port in both browsers.
 */
export function originPattern(url: string): string {
  const { protocol, hostname } = new URL(url)
  return `${protocol}//${hostname}/*`
}

export function isPlainHttp(url: string): boolean {
  return /^http:\/\//i.test(url.trim())
}

export const DEV_WEB_PORT = "3000"
export const DEV_API_PORT = "8000"

/**
 * Where the API might live under a server URL. The bundled app serves it under `/api`; a dev
 * backend serves it at the root. Either form of the URL may have been entered.
 *
 * In dev mode the address people open is the web app's own server on :3000, which serves no API
 * (it redirects every path to add a trailing slash). The web app reaches the API on :8000 of the
 * same host (`frontend/app/lib/api.ts`), so that is tried too.
 */
export function apiBaseCandidates(serverUrl: string): string[] {
  const candidates = serverUrl.endsWith("/api") ? [serverUrl, `${serverUrl}/api`] : [`${serverUrl}/api`, serverUrl]
  const { protocol, hostname, port } = new URL(serverUrl)
  if (port === DEV_WEB_PORT) candidates.push(`${protocol}//${hostname}:${DEV_API_PORT}`)
  return candidates
}

function isIdentity(body: unknown): body is Identity {
  return (
    isRecord(body) &&
    typeof body.username === "string" &&
    typeof body.token_name === "string" &&
    typeof body.app_version === "string"
  )
}

async function olderServerVersion(fetchImpl: FetchLike, bases: string[]): Promise<string | null> {
  for (const base of bases) {
    try {
      const reply = await sendRequest(fetchImpl, "GET", `${base}/ytdl/version`, null)
      if (reply.status === 200 && isRecord(reply.body) && typeof reply.body.app_version === "string") {
        return reply.body.app_version
      }
    } catch {
      return null
    }
  }
  return null
}

/** Finds the API under `serverUrl` and checks the token against it. */
export async function resolveConnection(
  fetchImpl: FetchLike,
  serverUrl: string,
  token: string
): Promise<Connection> {
  const origin = new URL(serverUrl).origin
  const bases = apiBaseCandidates(serverUrl)
  let missingEndpoint = false
  let lastReply: HttpReply | null = null

  for (const base of bases) {
    let reply: HttpReply
    try {
      reply = await sendRequest(fetchImpl, "GET", `${base}/extension/me`, token)
    } catch (error) {
      return { ok: false, message: describeFailure(error, origin) }
    }
    if (reply.status === 200 && isIdentity(reply.body)) {
      return { ok: true, apiBase: base, identity: reply.body }
    }
    if (reply.status === 404 && reply.json) {
      missingEndpoint = true
      continue
    }
    // A 200 that isn't the identity is the web app's HTML shell, which answers any non-API path.
    if (reply.status === 200) continue
    if (reply.json && (reply.status === 401 || reply.status === 429)) {
      return { ok: false, message: describeError(reply) }
    }
    lastReply = reply
  }

  if (missingEndpoint) {
    const version = await olderServerVersion(fetchImpl, bases)
    if (version) {
      return {
        ok: false,
        message:
          `This server runs YT-Hoarder ${version}, which doesn't accept browser-extension tokens. ` +
          `Update it to ${MIN_SERVER_VERSION} or later.`,
      }
    }
  }
  return {
    ok: false,
    message: lastReply ? describeError(lastReply) : `No YT-Hoarder server found at ${serverUrl}.`,
  }
}

/** A server timestamp as epoch milliseconds, or null when it isn't one. */
export function parseServerTime(text: string): number | null {
  // Some timestamps arrive naive (UTC with no zone), which Date would otherwise read as local time.
  const zoned = /(?:z|[+-]\d\d:?\d\d)$/i.test(text)
  const time = new Date(zoned ? text : `${text}Z`).getTime()
  return Number.isNaN(time) ? null : time
}

export function describeIdentity(identity: Identity): string {
  const parts = [
    `Connected as ${identity.username}`,
    `token "${identity.token_name}"`,
    `server v${identity.app_version}`,
  ]
  if (identity.expires_at) {
    const expires = parseServerTime(identity.expires_at)
    if (expires !== null) parts.push(`expires ${new Date(expires).toLocaleDateString()}`)
  }
  return parts.join(" · ")
}

export type TaskStatus =
  | "RESOLVING"
  | "QUEUED"
  | "IN_PROGRESS"
  | "POSTPROCESSING"
  | "RETRY"
  | "NOT_READY"
  | "COMPLETE"
  | "FAILED"

export type TaskKind = "download" | "transcript" | "clip" | "conversion"

/** One row of the popup's task list, from a server reply that has been checked. */
export type TaskView = {
  id: string
  title: string | null
  channel: string | null
  kind: TaskKind
  status: TaskStatus
  percent: number | null
  etaSeconds: number | null
  phase: "VIDEO" | "AUDIO" | null
  message: string | null
  errorCode: string | null
  retryAtMs: number | null
  sleepUntilMs: number | null
}

/**
 * What the task list asks of the background script. `fatal` replies won't improve by asking
 * again (a rejected token, a server too old to list tasks), so the caller stops polling on them.
 */
export type TaskFeed =
  | { ok: true; tasks: TaskView[]; hasMore: boolean }
  | { ok: false; message: string; fatal: boolean; retryAfterSeconds: number | null }

export const MAX_TASK_ROWS = 50
const MAX_TASK_TEXT = 300

const TASK_STATUSES: readonly string[] = [
  "RESOLVING",
  "QUEUED",
  "IN_PROGRESS",
  "POSTPROCESSING",
  "RETRY",
  "NOT_READY",
  "COMPLETE",
  "FAILED",
] satisfies TaskStatus[]

const TASK_KINDS: Record<string, TaskKind> = {
  DOWNLOAD: "download",
  TRANSCRIPT_GENERATION: "transcript",
  CLIP_GENERATION: "clip",
  MEDIA_CONVERSION: "conversion",
}

const TASKS_UNSUPPORTED_MESSAGE =
  "This server is too old to list tasks here. Update YT-Hoarder to see progress in the popup."
const TASKS_MALFORMED_MESSAGE = "The server's task list wasn't in the format this extension expects."

function taskText(value: unknown): string | null {
  if (typeof value !== "string" || value === "") return null
  return redactTokens(value.length > MAX_TASK_TEXT ? `${value.slice(0, MAX_TASK_TEXT)}…` : value)
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function taskTime(value: unknown): number | null {
  return typeof value === "string" ? parseServerTime(value) : null
}

function taskView(row: unknown): TaskView | null {
  if (!isRecord(row)) return null
  const id = taskText(row.task_id)
  const kind = typeof row.task_type === "string" ? TASK_KINDS[row.task_type] : undefined
  if (id === null || kind === undefined) return null
  if (typeof row.status !== "string" || !TASK_STATUSES.includes(row.status)) return null

  const percent = finiteNumber(row.percent_complete)
  const eta = finiteNumber(row.eta_seconds)
  return {
    id,
    title: taskText(row.title),
    channel: taskText(row.channel),
    kind,
    status: row.status as TaskStatus,
    percent: percent === null ? null : Math.min(100, Math.max(0, Math.round(percent))),
    etaSeconds: eta !== null && eta >= 0 ? eta : null,
    phase: row.download_phase === "VIDEO" || row.download_phase === "AUDIO" ? row.download_phase : null,
    message: taskText(row.status_message),
    errorCode: taskText(row.error_code),
    retryAtMs: taskTime(row.next_retry_at),
    sleepUntilMs: taskTime(row.sleep_until),
  }
}

/** Turns the `/extension/tasks` reply into rows the popup can draw, or says why it can't. */
export function describeTasks(reply: HttpReply): TaskFeed {
  if (!isSuccess(reply)) {
    // A wait or a server error passes; anything else (a refused token, a missing route, a
    // redirect, a proxy's HTML) would only repeat, and every refused token counts against the
    // server's per-address limit on failed sign-ins.
    const transient = !reply.redirected && (reply.status === 429 || reply.status >= 500)
    return {
      ok: false,
      message: reply.status === 404 && reply.json ? TASKS_UNSUPPORTED_MESSAGE : describeError(reply),
      fatal: !transient,
      retryAfterSeconds: retryAfterSeconds(reply),
    }
  }
  const { tasks, has_more: hasMore } = reply.body as Record<string, unknown>
  if (!Array.isArray(tasks)) {
    return { ok: false, message: TASKS_MALFORMED_MESSAGE, fatal: true, retryAfterSeconds: null }
  }
  return {
    ok: true,
    tasks: tasks.slice(0, MAX_TASK_ROWS).flatMap((row) => taskView(row) ?? []),
    hasMore: hasMore === true || tasks.length > MAX_TASK_ROWS,
  }
}
