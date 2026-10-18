/// <reference lib="webworker" />

/**
 * Offline service worker.
 *
 * Two jobs: keep the app shell available with the radio off, and answer media
 * requests out of IndexedDB so `<video>`/`<audio>` play and seek offline without
 * the player knowing anything about it (useMediaElement still points `src` at
 * /api/media/{id}).
 *
 * Bundled to out/sw.js by scripts/build-sw.mjs, which injects BUILD_ID and the
 * precache list. It is never served in dev -- the registrar only registers on a
 * production build, because a worker in front of `next dev` breaks HMR.
 */

import {
  fullResponseInit,
  parseRangeHeader,
  partialResponseInit,
  unsatisfiableResponseInit,
} from "@/app/lib/offlineRange"
import { getAsset, getItem, readRange, touchItem } from "@/app/lib/offlineDb"
import { classifyRequest } from "@/app/lib/offlineRoutes"

declare const self: ServiceWorkerGlobalScope

/** Replaced at bundle time; the literal here is only what a stray dev build would see. */
declare const __BUILD_ID__: string
declare const __PRECACHE__: string[]

const CACHE_NAME = `yt-hoarder-shell-${__BUILD_ID__}`
const SHELL_URL = "/"

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll([SHELL_URL, ...__PRECACHE__]))
  )
})

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys()
      await Promise.all(
        names
          .filter((name) => name.startsWith("yt-hoarder-shell-") && name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      )
      await self.clients.claim()
    })()
  )
})

self.addEventListener("message", (event) => {
  // The page decides when a waiting worker takes over, so a reload never happens
  // underneath someone mid-playback.
  if (event.data === "skip-waiting") void self.skipWaiting()
})

/**
 * Serve a byte range from IndexedDB.
 *
 * Returns null whenever anything is missing so the caller falls through to the
 * network: a half-downloaded item must not be answered with a hole, and the
 * downloader's own ranged fetches pass straight through this way with no
 * special-casing.
 */
async function respondFromStorage(request: Request, mediaId: number): Promise<Response | null> {
  const item = await getItem(mediaId)
  if (!item || !item.complete) return null

  const rangeHeader = request.headers.get("range")
  if (rangeHeader === null) {
    const blob = await readRange(mediaId, { start: 0, end: item.bytes - 1 })
    if (!blob) return null
    void touchItem(mediaId)
    const init = fullResponseInit(item.bytes, item.contentType)
    return new Response(blob, init)
  }

  const range = parseRangeHeader(rangeHeader, item.bytes)
  if (!range) return new Response(null, unsatisfiableResponseInit(item.bytes))

  const blob = await readRange(mediaId, range)
  if (!blob) return null
  void touchItem(mediaId)
  return new Response(blob, partialResponseInit(range, item.bytes, item.contentType))
}

async function respondWithAsset(
  mediaId: number,
  kind: Parameters<typeof getAsset>[1]
): Promise<Response | null> {
  const asset = await getAsset(mediaId, kind)
  if (!asset) return null
  return new Response(asset.blob, {
    status: 200,
    headers: { "Content-Type": asset.contentType, "Cache-Control": "no-store" },
  })
}

async function respondWithShell(request: Request): Promise<Response> {
  const cached = await caches.match(SHELL_URL)
  if (cached) return cached
  return fetch(request)
}

async function respondCacheFirst(request: Request): Promise<Response> {
  const cached = await caches.match(request)
  if (cached) return cached

  const response = await fetch(request)
  // Content-hashed under /_next/static, so a 200 is safe to keep indefinitely and
  // an error must not be cached in its place.
  if (response.ok) {
    const cache = await caches.open(CACHE_NAME)
    await cache.put(request, response.clone())
  }
  return response
}

self.addEventListener("fetch", (event) => {
  const request = event.request
  const url = new URL(request.url)

  const route = classifyRequest({
    pathname: url.pathname,
    method: request.method,
    sameOrigin: url.origin === self.location.origin,
    isNavigation: request.mode === "navigate",
  })

  switch (route.kind) {
    case "passthrough":
      // Returning without calling respondWith leaves the request to the network.
      return
    case "media": {
      const media = route.media
      event.respondWith(
        (async () => {
          const local =
            media.kind === "stream"
              ? await respondFromStorage(request, media.mediaId)
              : await respondWithAsset(media.mediaId, media.asset)
          return local ?? fetch(request)
        })()
      )
      return
    }
    case "shell":
      event.respondWith(respondWithShell(request))
      return
    case "static":
      event.respondWith(respondCacheFirst(request))
  }
})
