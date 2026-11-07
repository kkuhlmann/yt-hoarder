/**
 * The options the web app's download and subscription forms offer
 * (`frontend/app/_components/downloadOptions.tsx`), as values the server's `DownloadQuality` and
 * `AudioQuality` enums accept, plus the conversions those forms apply before sending.
 */

export const VIDEO_QUALITIES = [
  { value: "BEST", label: "Best" },
  { value: "1440P", label: "1440p" },
  { value: "1080P", label: "1080p" },
  { value: "720P", label: "720p" },
  { value: "480P", label: "480p" },
  { value: "360P", label: "360p" },
] as const

export const AUDIO_QUALITIES = [
  { value: "BEST", label: "Best" },
  { value: "128K", label: "128 kbps" },
  { value: "96K", label: "96 kbps" },
  { value: "64K", label: "64 kbps" },
  { value: "48K", label: "48 kbps" },
] as const

export type VideoQuality = (typeof VIDEO_QUALITIES)[number]["value"]
export type AudioQuality = (typeof AUDIO_QUALITIES)[number]["value"]

export function isVideoQuality(value: unknown): value is VideoQuality {
  return VIDEO_QUALITIES.some((choice) => choice.value === value)
}

export function isAudioQuality(value: unknown): value is AudioQuality {
  return AUDIO_QUALITIES.some((choice) => choice.value === value)
}

/** Minutes typed into a length box as whole seconds: null when blank, undefined when not a length. */
export function durationSeconds(minutes: string): number | null | undefined {
  const text = minutes.trim()
  if (!text) return null
  const value = Number(text)
  if (!Number.isFinite(value) || value < 0) return undefined
  return Math.round(value * 60)
}

/**
 * An `<input type="date">` value as the app's date picker sends it: that day's local midnight as a
 * UTC ISO timestamp. The server skips uploads released at or before it.
 */
export function uploadedAfterTimestamp(date: string): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date)
  if (!match) return null
  const [year, month, day] = match.slice(1).map(Number)
  return new Date(year, month - 1, day).toISOString()
}
