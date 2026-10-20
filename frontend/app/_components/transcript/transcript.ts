import type { RefObject } from "react"

/** `GET /media-details/{id}/transcript`. */
export type TranscriptDocument = {
  media: {
    id: number
    url: string
    title: string | null
    channel: string | null
    media_type: string
    status: string
    duration: number | null
    release_timestamp: string | null
    thumbnail_path: string | null
    playable: boolean
    /** The viewer's own. */
    rating: number | null
    access_count: number
    /** Where the viewer left off, null if never played. */
    playback_position: number | null
    owner_id: number | null
    /** The latest transcription job, null if none ran. */
    transcript_task_status: string | null
    transcript_task_progress: number | null
  }
  youtube_id: string | null
  blocks: { id: number; start_time: number; end_time: number; text: string }[]
}

export type TranscriptBlock = TranscriptDocument["blocks"][number]

/**
 * What to open a no-file media page on. `seconds` null means no moment was asked for (a
 * row, or View transcript), so it resumes where the user left off; a search hit or a deep
 * link names its moment and opens exactly there.
 */
export type TranscriptTarget = { mediaId: number; seconds: number | null }

/** Set by whichever player is mounted, so a click on a passage can move it. */
export type SeekRef = RefObject<((seconds: number) => void) | null>

/**
 * Where a resume starts, on the app player's rule (useMediaElement): a position in the
 * last 30 s restarts from the beginning, so a finished video doesn't reopen on its end.
 */
export function resumePosition(media: TranscriptDocument["media"]): number {
  const saved = media.playback_position ?? 0
  return media.duration && saved > media.duration - 30 ? 0 : saved
}

export type PageKind = "video" | "audio" | "embed"

/**
 * Which media page an item opens on. A downloaded file plays from the app; anything
 * without one (a transcript-only item, deleted media that kept its transcript) gets the
 * embed page, which shows YouTube or, for another site, a link out. `file_path` absent
 * means the caller doesn't know it (a search hit), and a COMPLETE row has one.
 */
export function pageKindFor(media: {
  media_type: string
  status?: string
  file_path?: string | null
}): PageKind {
  const noFile =
    media.media_type === "TRANSCRIPT" ||
    (media.status !== undefined && media.status !== "COMPLETE") ||
    media.file_path === null
  if (noFile) return "embed"
  return media.media_type === "VIDEO" ? "video" : "audio"
}

const TRANSCRIBING_STATUSES = new Set(["QUEUED", "IN_PROGRESS", "RETRY"])

export function isTranscribing(doc: TranscriptDocument | null): boolean {
  return doc !== null && TRANSCRIBING_STATUSES.has(doc.media.transcript_task_status ?? "")
}

/** What the media page's transcript button offers. */
export type TranscriptButtonState = "show" | "generate" | "working" | "none"

/**
 * Show once there is a transcript; otherwise Generate, but only where the server would
 * accept it (a file to transcribe, and the owner or an admin asking); Working while a job
 * runs. Nothing offline, where every server-touching control is hidden.
 */
export function transcriptButtonState(
  doc: TranscriptDocument | null,
  user: { id?: number; is_admin?: boolean } | null | undefined,
  offline: boolean
): TranscriptButtonState {
  if (offline || !doc) return "none"
  if (doc.blocks.length > 0) return "show"
  if (isTranscribing(doc)) return "working"
  const owner = doc.media.owner_id !== null && doc.media.owner_id === user?.id
  return doc.media.playable && (owner || user?.is_admin) ? "generate" : "none"
}

/** The words typed into a transcript's search box, lowercased, punctuation trimmed. */
export function transcriptQueryTerms(query: string): string[] {
  const terms = query
    .toLowerCase()
    .split(/\s+/)
    .map((term) => term.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, ""))
    .filter(Boolean)
  return [...new Set(terms)]
}

/**
 * The passages containing every term, each with its index in the full transcript so the
 * playing passage can still be marked inside a filtered list.
 */
export function filterTranscriptBlocks(
  blocks: TranscriptBlock[],
  terms: string[]
): { block: TranscriptBlock; index: number }[] {
  const indexed = blocks.map((block, index) => ({ block, index }))
  if (terms.length === 0) return indexed
  return indexed.filter(({ block }) => {
    const text = block.text.toLowerCase()
    return terms.every((term) => text.includes(term))
  })
}

/** Index of the block playing at `seconds`: the last one started by then. */
export function activeBlockIndex(blocks: TranscriptDocument["blocks"], seconds: number): number {
  let active = 0
  for (let i = 0; i < blocks.length; i++) {
    if (blocks[i].start_time <= seconds) active = i
    else break
  }
  return active
}

/** The source URL opened at `seconds`, for the link-out. */
export function sourceUrlAt(url: string, seconds: number): string {
  try {
    const parsed = new URL(url)
    parsed.searchParams.set("t", String(Math.floor(seconds)))
    return parsed.toString()
  } catch {
    return url
  }
}

/** YouTube's `playerState` for playing. */
const YOUTUBE_PLAYING = 1

/**
 * What a YouTube embed's message says about playback, or null for any other message.
 * Position and play state can arrive in separate messages, so either may be absent.
 */
export function parsePlayerInfo(data: unknown): { currentTime?: number; playing?: boolean } | null {
  let message: unknown = data
  if (typeof data === "string") {
    try {
      message = JSON.parse(data)
    } catch {
      return null
    }
  }
  const info = (message as { info?: unknown } | null)?.info
  if (!info || typeof info !== "object") return null
  const { currentTime, playerState } = info as { currentTime?: unknown; playerState?: unknown }
  return {
    ...(typeof currentTime === "number" && { currentTime }),
    ...(typeof playerState === "number" && { playing: playerState === YOUTUBE_PLAYING }),
  }
}

/**
 * Whether to record the position, on the app player's rule (useMediaElement): every 5 s
 * of movement, starting with the first report once something is playing.
 */
export function positionSaveDue(lastSaved: number | null, seconds: number): boolean {
  return lastSaved === null || Math.abs(seconds - lastSaved) >= 5
}
