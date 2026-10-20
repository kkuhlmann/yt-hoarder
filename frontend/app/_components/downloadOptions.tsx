"use client"

import { Checkbox } from "@/components/ui/checkbox"
import { cn } from "@/lib/utils"
import type { SubscriptionType } from "@/app/types/SubscriptionsOptions"
import type { DownloadOptionsType } from "@/app/types/DownloadsOptions"

export const INITIAL_DOWNLOAD_OPTIONS: DownloadOptionsType = {
  download_playlist: false,
  audio_only: false,
  transcript_only: false,
  overwrite: false,
  media_type: 0,
  url: "",
  generate_transcript: false,
  download_quality: "BEST",
  audio_quality: "BEST",
}

export const QUALITY_OPTIONS = [
  { value: "BEST", label: "Best" },
  { value: "1440P", label: "1440p" },
  { value: "1080P", label: "1080p" },
  { value: "720P", label: "720p" },
  { value: "480P", label: "480p" },
  { value: "360P", label: "360p" },
]

export const AUDIO_QUALITY_OPTIONS = [
  { value: "BEST", label: "Best" },
  { value: "128K", label: "128 kbps" },
  { value: "96K", label: "96 kbps" },
  { value: "64K", label: "64 kbps" },
  { value: "48K", label: "48 kbps" },
]

/**
 * The media_type a download or subscription form submits. Transcript only wins over Audio
 * Only: the server makes a transcript-only request audio with a transcript regardless.
 */
export const formMediaType = (options: { transcript_only?: boolean; audio_only: boolean }) =>
  options.transcript_only ? "TRANSCRIPT" : options.audio_only ? "AUDIO" : "VIDEO"

export const TRANSCRIPT_ONLY_HINT =
  "Index the spoken transcript for search without keeping the media. Uses YouTube captions, " +
  "or downloads the audio for Whisper and deletes it afterwards."

export const qualityLabel = (value: string | undefined, audio: boolean): string => {
  const options = audio ? AUDIO_QUALITY_OPTIONS : QUALITY_OPTIONS
  return options.find((o) => o.value === value)?.label ?? value ?? ""
}

export function OptionToggle({
  label,
  checked,
  onChange,
  title,
}: {
  label: string
  checked: boolean
  onChange: () => void
  title?: string
}) {
  return (
    <label
      title={title}
      className={cn(
        "flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-mono transition-all cursor-pointer",
        checked
          ? "bg-bg-surface border border-border"
          : "bg-transparent border border-transparent opacity-70 hover:opacity-100"
      )}
    >
      <Checkbox checked={checked} onCheckedChange={onChange} />
      <span className="text-text-secondary">{label}</span>
    </label>
  )
}

export const DEFAULT_SUBSCRIPTION: SubscriptionType = {
  id: 0,
  url: "",
  enabled: true,
  audio_only: false,
  media_type: "VIDEO",
  string_match: "",
  overwrite: false,
  date_filter: new Date(),
  min_duration_seconds: null,
  max_duration_seconds: null,
  channel: "",
  generate_transcript: false,
  download_quality: "BEST",
  audio_quality: "BEST",
}
