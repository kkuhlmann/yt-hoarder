/**
 * IndexedDB store backing offline playback.
 *
 * Media is held as ~4 MiB blob chunks rather than whole responses for two reasons
 * the Cache API can't cover: `cache.put()` rejects the 206 that every ranged media
 * request produces, and a phone will background the app mid-download, so a download
 * has to resume from the last chunk it finished instead of starting over. Chunked
 * rows also give exact per-item byte accounting, which the storage budget needs.
 *
 * Like offlineRange, this is imported by the service worker and must not touch
 * `window`/`document` — sw/tsconfig.json compiles without the DOM lib.
 */

import { CHUNK_SIZE, chunkIndexRange, sliceWithinChunks, type ByteRange } from "./offlineRange"

const DB_NAME = "yt-hoarder-offline"
// Bumping this is the whole migration: every store below is created under an
// `if (!contains)` guard, so an upgrade only ever adds what a version lacks.
const DB_VERSION = 2

export const STORE_ITEMS = "items"
export const STORE_CHUNKS = "chunks"
export const STORE_META = "meta"
export const STORE_ASSETS = "assets"
export const STORE_OUTBOX = "outbox"
export const STORE_KV = "kv"
export const STORE_PLAYLISTS = "playlists"

/**
 * Where an item came from, which decides whether the budget may evict it.
 * `manual` is a user pin and is never evicted; the collection sources are the LRU pool.
 */
export type OfflineSource = "manual" | `playlist:${number}` | `subscription:${number}`

export type OfflineItem = {
  id: number
  pinned: boolean
  source: OfflineSource
  /** Total size of the media file, from MediaDetails.file_size_bytes. */
  bytes: number
  /** Captured from the origin's own response so we never re-derive the MIME type. */
  contentType: string
  chunkCount: number
  chunksStored: number
  complete: boolean
  addedAt: number
  lastPlayedAt: number
}

export type OfflineAssetKind = "thumbnail" | "sprites" | "spriteMetadata" | "peaks"

export type OfflineAsset = {
  mediaId: number
  kind: OfflineAssetKind
  blob: Blob
  contentType: string
}

/**
 * A pending playback-position write.
 *
 * Keyed by mediaId rather than an autoincrement id so a later position simply
 * replaces the earlier one: the player saves every 5 seconds of drift, which over
 * a two-hour film would otherwise queue well over a thousand rows to send for a
 * single resume point.
 */
export type OutboxEntry = {
  mediaId: number
  playbackPosition: number
  lastAccessed: string
}

/** The serialized media record from the API, kept so the library renders offline. */
export type OfflineMeta = { id: number; record: Record<string, unknown> }

/**
 * A playlist as last seen online: the list record plus its full ordered membership.
 *
 * Membership has to be its own snapshot because nothing else holds it — the media
 * record carries no playlist ids, and an item's `source` names at most one
 * collection and is overwritten to `manual` on a hand download. Which of these
 * playlists is worth showing offline is decided at read time against the items
 * store, so the snapshot itself is just server truth, unfiltered.
 */
export type OfflinePlaylist = {
  id: number
  name: string
  description: string | null
  source_url: string | null
  created_at: string
  updated_at: string
  /** Every member, in playlist order. */
  mediaIds: number[]
  snapshotAt: number
}

let dbPromise: Promise<IDBDatabase> | null = null

export function openOfflineDb(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise

  dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
    const open = indexedDB.open(DB_NAME, DB_VERSION)

    open.onupgradeneeded = () => {
      const db = open.result
      if (!db.objectStoreNames.contains(STORE_ITEMS)) {
        const items = db.createObjectStore(STORE_ITEMS, { keyPath: "id" })
        items.createIndex("by-source", "source")
        items.createIndex("by-last-played", "lastPlayedAt")
      }
      if (!db.objectStoreNames.contains(STORE_CHUNKS)) {
        db.createObjectStore(STORE_CHUNKS, { keyPath: ["mediaId", "index"] })
      }
      if (!db.objectStoreNames.contains(STORE_META)) {
        db.createObjectStore(STORE_META, { keyPath: "id" })
      }
      if (!db.objectStoreNames.contains(STORE_ASSETS)) {
        db.createObjectStore(STORE_ASSETS, { keyPath: ["mediaId", "kind"] })
      }
      if (!db.objectStoreNames.contains(STORE_OUTBOX)) {
        db.createObjectStore(STORE_OUTBOX, { keyPath: "mediaId" })
      }
      if (!db.objectStoreNames.contains(STORE_KV)) {
        db.createObjectStore(STORE_KV, { keyPath: "key" })
      }
      if (!db.objectStoreNames.contains(STORE_PLAYLISTS)) {
        db.createObjectStore(STORE_PLAYLISTS, { keyPath: "id" })
      }
    }

    open.onsuccess = () => resolve(open.result)
    open.onerror = () => reject(open.error)
    // Another tab holds an old version open; failing loudly beats hanging forever.
    open.onblocked = () => reject(new Error("offline database upgrade blocked by another tab"))
  }).catch((error) => {
    // Don't cache a rejected promise — a private-mode or quota failure on one call
    // shouldn't permanently poison every later one.
    dbPromise = null
    throw error
  })

  return dbPromise
}

function promisify<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

async function withStore<T>(
  storeNames: string | string[],
  mode: IDBTransactionMode,
  run: (tx: IDBTransaction) => Promise<T> | T
): Promise<T> {
  const db = await openOfflineDb()
  const tx = db.transaction(storeNames, mode)
  const done = new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error ?? new Error("offline transaction aborted"))
  })
  const result = await run(tx)
  // Readwrite transactions must be awaited to completion, or a caller that reads
  // straight back can observe a write that has not yet been durably applied.
  if (mode !== "readonly") await done
  return result
}

// --- items -----------------------------------------------------------------

export function getItem(id: number): Promise<OfflineItem | undefined> {
  return withStore(STORE_ITEMS, "readonly", (tx) =>
    promisify<OfflineItem | undefined>(tx.objectStore(STORE_ITEMS).get(id))
  )
}

export function putItem(item: OfflineItem): Promise<void> {
  return withStore(STORE_ITEMS, "readwrite", (tx) => {
    tx.objectStore(STORE_ITEMS).put(item)
  })
}

export function allItems(): Promise<OfflineItem[]> {
  return withStore(STORE_ITEMS, "readonly", (tx) =>
    promisify<OfflineItem[]>(tx.objectStore(STORE_ITEMS).getAll())
  )
}

/**
 * Records real playback, which is what the LRU orders on.
 *
 * The read and the write share one transaction, and the update is issued from
 * inside the cursor callback so nothing can interleave. The worker touches an
 * item on every range it serves, so a read-modify-write spanning two
 * transactions lets a delete land between them and be undone by the write —
 * resurrecting an item row whose chunks are gone, which then charges the budget
 * for bytes that no longer exist.
 */
export function touchItem(id: number): Promise<void> {
  return withStore(STORE_ITEMS, "readwrite", (tx) => {
    const request = tx.objectStore(STORE_ITEMS).openCursor(IDBKeyRange.only(id))
    request.onsuccess = () => {
      const cursor = request.result
      if (cursor) cursor.update({ ...(cursor.value as OfflineItem), lastPlayedAt: Date.now() })
    }
  })
}

export async function deleteItem(id: number): Promise<void> {
  await withStore(
    [STORE_ITEMS, STORE_CHUNKS, STORE_META, STORE_ASSETS],
    "readwrite",
    async (tx) => {
      tx.objectStore(STORE_ITEMS).delete(id)
      tx.objectStore(STORE_META).delete(id)
      // Compound keys are ordered by component, so a bound over [id, *] covers
      // exactly this item's rows in both stores.
      tx.objectStore(STORE_CHUNKS).delete(IDBKeyRange.bound([id, -Infinity], [id, Infinity]))
      tx.objectStore(STORE_ASSETS).delete(IDBKeyRange.bound([id, ""], [id, "￿"]))
    }
  )
}

/** Bytes an item actually occupies, counting a partial download at what it has stored. */
export function storedBytesOf(item: OfflineItem): number {
  if (item.complete) return item.bytes
  return Math.min(item.chunksStored * CHUNK_SIZE, item.bytes)
}

export async function usedBytes(): Promise<number> {
  const items = await allItems()
  return items.reduce((total, item) => total + storedBytesOf(item), 0)
}

// --- chunks ----------------------------------------------------------------

export function putChunk(mediaId: number, index: number, blob: Blob): Promise<void> {
  return withStore(STORE_CHUNKS, "readwrite", (tx) => {
    tx.objectStore(STORE_CHUNKS).put({ mediaId, index, blob })
  })
}

/**
 * Assemble the bytes covering `range`. Returns null when any needed chunk is
 * missing, so the caller can fall through to the network rather than serve a hole.
 */
export async function readRange(mediaId: number, range: ByteRange): Promise<Blob | null> {
  const { first, last } = chunkIndexRange(range)
  const rows = await withStore(STORE_CHUNKS, "readonly", (tx) =>
    promisify<{ mediaId: number; index: number; blob: Blob }[]>(
      tx.objectStore(STORE_CHUNKS).getAll(IDBKeyRange.bound([mediaId, first], [mediaId, last]))
    )
  )

  if (rows.length !== last - first + 1) return null

  rows.sort((a, b) => a.index - b.index)
  const { offset, length } = sliceWithinChunks(range)
  return new Blob(rows.map((row) => row.blob)).slice(offset, offset + length)
}

// --- metadata, assets, outbox, kv ------------------------------------------

export function putMeta(id: number, record: Record<string, unknown>): Promise<void> {
  return withStore(STORE_META, "readwrite", (tx) => {
    tx.objectStore(STORE_META).put({ id, record })
  })
}

export function allMeta(): Promise<OfflineMeta[]> {
  return withStore(STORE_META, "readonly", (tx) =>
    promisify<OfflineMeta[]>(tx.objectStore(STORE_META).getAll())
  )
}

export function putAsset(asset: OfflineAsset): Promise<void> {
  return withStore(STORE_ASSETS, "readwrite", (tx) => {
    tx.objectStore(STORE_ASSETS).put(asset)
  })
}

export function getAsset(
  mediaId: number,
  kind: OfflineAssetKind
): Promise<OfflineAsset | undefined> {
  return withStore(STORE_ASSETS, "readonly", (tx) =>
    promisify<OfflineAsset | undefined>(tx.objectStore(STORE_ASSETS).get([mediaId, kind]))
  )
}

export function enqueueOutbox(entry: OutboxEntry): Promise<void> {
  return withStore(STORE_OUTBOX, "readwrite", (tx) => {
    tx.objectStore(STORE_OUTBOX).put(entry)
  })
}

export function readOutbox(): Promise<OutboxEntry[]> {
  return withStore(STORE_OUTBOX, "readonly", (tx) =>
    promisify<OutboxEntry[]>(tx.objectStore(STORE_OUTBOX).getAll())
  )
}

export function clearOutboxEntries(mediaIds: number[]): Promise<void> {
  return withStore(STORE_OUTBOX, "readwrite", (tx) => {
    const store = tx.objectStore(STORE_OUTBOX)
    for (const mediaId of mediaIds) store.delete(mediaId)
  })
}

export async function getKv<T>(key: string): Promise<T | undefined> {
  const row = await withStore(STORE_KV, "readonly", (tx) =>
    promisify<{ key: string; value: T } | undefined>(tx.objectStore(STORE_KV).get(key))
  )
  return row?.value
}

export function setKv<T>(key: string, value: T): Promise<void> {
  return withStore(STORE_KV, "readwrite", (tx) => {
    tx.objectStore(STORE_KV).put({ key, value })
  })
}

// --- playlists -------------------------------------------------------------

/**
 * Swap the whole snapshot in one transaction, so a playlist deleted upstream
 * disappears here too and a failure part-way leaves the previous snapshot intact
 * rather than a half-written one.
 */
export function replacePlaylists(playlists: OfflinePlaylist[]): Promise<void> {
  return withStore(STORE_PLAYLISTS, "readwrite", (tx) => {
    const store = tx.objectStore(STORE_PLAYLISTS)
    store.clear()
    for (const playlist of playlists) store.put(playlist)
  })
}

export function allPlaylists(): Promise<OfflinePlaylist[]> {
  return withStore(STORE_PLAYLISTS, "readonly", (tx) =>
    promisify<OfflinePlaylist[]>(tx.objectStore(STORE_PLAYLISTS).getAll())
  )
}
