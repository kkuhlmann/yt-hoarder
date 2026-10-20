"use client"

import { FolderIcon } from "@heroicons/react/20/solid"
import { CardShell } from "./data/CardGrid"
import { Collage } from "./data/Thumb"
import { formatBytes, formatDurationCompact } from "@/app/utils"
import type { MediaGroup } from "@/app/types/DownloadsOptions"

function fmtMonthYear(iso: string | null): string {
  if (!iso) return ""
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z")
  return d.toLocaleDateString(undefined, { month: "short", year: "numeric" })
}

function dateRange(min: string | null, max: string | null): string {
  const a = fmtMonthYear(min)
  const b = fmtMonthYear(max)
  if (!a && !b) return ""
  if (!a) return b
  if (!b || a === b) return a
  return `${a} – ${b}`
}

export function GroupFolderCard({
  group,
  index,
  onClick,
}: {
  group: MediaGroup
  index: number
  onClick: () => void
}) {
  const avParts: string[] = []
  if (group.video_count) avParts.push(`${group.video_count} video`)
  if (group.audio_count) avParts.push(`${group.audio_count} audio`)
  if (group.transcript_count) avParts.push(`${group.transcript_count} transcript only`)
  const range = dateRange(group.min_date, group.max_date)

  return (
    <CardShell
      index={index}
      onClick={onClick}
      thumbnail={<Collage mediaIds={group.sample_media_ids} />}
      thumbnailOverlay={
        <span className="absolute top-1.5 left-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-black/70 text-[11px] font-mono text-matrix leading-none">
          <FolderIcon className="h-3 w-3" />
          {group.count}
        </span>
      }
    >
        <h3
          className="text-sm text-text-primary font-medium leading-tight line-clamp-2"
          title={group.label}
        >
          {group.label}
        </h3>

        <div className="flex items-center gap-1.5 text-[11px] text-text-secondary font-mono flex-wrap mt-auto">
          <span className="text-matrix">{group.count} items</span>
          {group.total_duration > 0 && (
            <>
              <span className="text-text-muted">·</span>
              <span>{formatDurationCompact(group.total_duration)}</span>
            </>
          )}
          {group.total_size_bytes > 0 && (
            <>
              <span className="text-text-muted">·</span>
              <span>{formatBytes(group.total_size_bytes)}</span>
            </>
          )}
        </div>

        {(avParts.length > 0 || range) && (
          <div className="flex items-center gap-1.5 text-[10px] text-text-muted font-mono flex-wrap">
            {avParts.length > 0 && <span>{avParts.join(" · ")}</span>}
            {avParts.length > 0 && range && <span>·</span>}
            {range && <span>{range}</span>}
          </div>
        )}
    </CardShell>
  )
}
