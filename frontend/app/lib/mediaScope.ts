/**
 * The media list's scopes. Every scope but Transcripts is a MediaDetails status; the
 * Transcripts view is everything the user can search, of any status and media type, so
 * it is a different query rather than a different status.
 */
export const TRANSCRIPTS_SCOPE = "TRANSCRIPTS"

/** The list / groups / stats query params a scope stands for. */
export function scopeParams(scope: string | null | undefined): Record<string, string | boolean> {
  if (!scope) return {}
  return scope === TRANSCRIPTS_SCOPE ? { has_transcript: true } : { status: scope }
}

/** Where a Transcripts-view row's transcript lives. */
export type TranscriptOrigin = "transcript-only" | "library" | "deleted"

export function transcriptOrigin(row: { media_type: string; status?: string }): TranscriptOrigin {
  if (row.media_type === "TRANSCRIPT") return "transcript-only"
  return row.status === "DELETED" ? "deleted" : "library"
}

export const TRANSCRIPT_ORIGIN_LABELS: Record<TranscriptOrigin, string> = {
  "transcript-only": "Transcript only",
  library: "In library",
  deleted: "Deleted",
}

/**
 * A row's type badge. The Transcripts view says where each transcript lives, which is
 * what a mixed list needs; the media type alone can't tell a deleted video from a kept one.
 */
export function mediaTypeLabel(
  scope: string,
  row: { media_type: string; status?: string },
): string {
  return scope === TRANSCRIPTS_SCOPE ? TRANSCRIPT_ORIGIN_LABELS[transcriptOrigin(row)] : row.media_type
}

/** The Transcripts view mixes in deleted media, which, as in the Library, isn't rated. */
export function isRatable(scope: string, row: { status?: string }): boolean {
  return scope === "COMPLETE" || (scope === TRANSCRIPTS_SCOPE && row.status === "COMPLETE")
}
