import type { HttpReply, MediaOptions } from "./api"

export const TOKEN = "ytdlh_SuperSecretTokenValue0123456789abcdefghij"

/** Every media option at its default, for building requests in tests. */
export const DEFAULT_OPTIONS: MediaOptions = {
  mediaType: "VIDEO",
  transcribe: false,
  videoQuality: "BEST",
  audioQuality: "BEST",
}

export const NO_FILTERS = {
  titleFilter: "",
  uploadedAfter: null,
  minDurationSeconds: null,
  maxDurationSeconds: null,
} as const

export function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  })
}

export function htmlResponse(status: number): Response {
  return new Response("<!doctype html><title>YT-Hoarder</title>", {
    status,
    headers: { "content-type": "text/html; charset=utf-8" },
  })
}

export function redirectResponse(): Response {
  return {
    type: "opaqueredirect",
    status: 0,
    headers: new Headers(),
    json: async () => {
      throw new SyntaxError("no body")
    },
  } as unknown as Response
}

export function reply(status: number, body: unknown, extra: Partial<HttpReply> = {}): HttpReply {
  return { status, redirected: false, retryAfter: null, json: true, body, ...extra }
}

type Route = Response | Error | ((init: RequestInit) => Response)

/** A fetch that answers by URL and records every call. */
export function routedFetch(routes: Record<string, Route>) {
  const calls: { url: string; init: RequestInit }[] = []
  const fetchImpl = async (url: string, init: RequestInit): Promise<Response> => {
    calls.push({ url, init })
    const route = routes[url]
    if (route === undefined) return jsonResponse(404, { detail: "Not Found" })
    if (route instanceof Error) throw route
    if (typeof route === "function") return route(init)
    return route.clone()
  }
  return { fetchImpl, calls }
}

/** An in-memory stand-in for `chrome.storage.local`. */
export function fakeStorageArea(initial: Record<string, unknown> = {}) {
  const data = new Map(Object.entries(structuredClone(initial)))
  return {
    data,
    async get(keys: string[]): Promise<Record<string, unknown>> {
      const result: Record<string, unknown> = {}
      for (const key of keys) if (data.has(key)) result[key] = structuredClone(data.get(key))
      return result
    },
    async set(items: Record<string, unknown>): Promise<void> {
      for (const [key, value] of Object.entries(items)) data.set(key, structuredClone(value))
    },
  }
}
