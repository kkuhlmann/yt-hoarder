"use client"

import React from "react"

import { CardShell } from "@/app/_components/data/CardGrid"
import { CardActionOverlay } from "@/app/_components/data/CardActionOverlay"
import { Thumb, ThumbPlaceholder } from "@/app/_components/data/Thumb"
import { formatDuration, formatRelativeTime } from "@/app/utils"
import type { Download } from "@/app/types/DownloadsOptions"

export function MediaCard<T extends Download>({
  row,
  index,
  onClick,
  actions,
  ratingSlot,
  badge,
  typeLabel = row.media_type,
  className,
  showProgress = true,
}: {
  row: T
  index: number
  onClick?: () => void
  actions: React.ReactNode
  ratingSlot?: React.ReactNode
  /** Extra top-left badge, e.g. a playlist position. */
  badge?: React.ReactNode
  /** The type chip's text; defaults to the media type. */
  typeLabel?: string
  className?: string
  /** The saved-position bar. See MediaListView's showPlaybackProgress. */
  showProgress?: boolean
}) {
  const progressPercentage =
    showProgress && row.duration && row.playback_position
      ? Math.min((row.playback_position / row.duration) * 100, 100)
      : 0

  return (
    <CardShell
      index={index}
      onClick={onClick}
      className={className}
      thumbnail={
        row.thumbnail_path ? (
          <Thumb
            mediaId={row.media_details_id}
            alt={row.title}
            mediaType={row.media_type}
          />
        ) : (
          <ThumbPlaceholder mediaType={row.media_type} />
        )
      }
      thumbnailOverlay={
        <>
          {/* Media facts live on the thumbnail rather than costing a body row.
              Both sit along the top so the bottom edge belongs to the actions. */}
          <span className="absolute top-1.5 left-1.5 flex items-center gap-1">
            {badge}
            <span className="px-1.5 py-0.5 rounded bg-black/75 text-[10px] font-mono text-white leading-none">
              {typeLabel}
            </span>
          </span>
          {row.duration != null && (
            <span className="absolute top-1.5 right-1.5 px-1.5 py-0.5 rounded bg-black/75 text-[11px] font-mono text-white leading-none">
              {formatDuration(row.duration)}
            </span>
          )}

          <CardActionOverlay actions={actions} />

          {progressPercentage > 0 && (
            <div className="absolute inset-x-0 bottom-0 h-[3px] bg-black/40">
              <div
                className="h-full bg-matrix"
                style={{ width: `${progressPercentage}%` }}
              />
            </div>
          )}
        </>
      }
    >
      {/* Title — min-h reserves both lines so the meta lines below stay
          aligned across the cards in a row */}
      <h3
        className="text-sm text-text-primary font-medium leading-tight line-clamp-2 min-h-8.5"
        title={row.title}
      >
        {row.title}
      </h3>

      <p className="flex items-baseline gap-1 text-xs text-text-secondary">
        <span className="truncate">{row.channel}</span>
        {row.release_timestamp && (
          <span className="shrink-0 text-text-muted">
            · {formatRelativeTime(row.release_timestamp)}
          </span>
        )}
      </p>

      {/* Rating + tags row, held to one line: flex-wrap pushes the tags that
          don't fit onto a second line and max-h clips it, so the break always
          lands between pills and wider cards show more tags */}
      {(ratingSlot || (row.tags && row.tags.length > 0)) && (
        <div
          className="flex items-center gap-1.5 flex-wrap max-h-[18px] overflow-hidden"
          onClick={(e) => e.stopPropagation()}
        >
          {ratingSlot}
          {row.tags &&
            row.tags.slice(0, 3).map((tag) => (
              <span
                key={tag.id}
                className="inline-flex px-1 py-0 rounded-full bg-matrix/15 text-matrix text-[10px] font-mono border border-matrix/20"
              >
                {tag.name}
              </span>
            ))}
          {row.tags && row.tags.length > 3 && (
            <span className="text-[10px] text-text-muted font-mono">
              +{row.tags.length - 3}
            </span>
          )}
        </div>
      )}
    </CardShell>
  )
}
