"use client"

import React from "react"

import {
  Bars2Icon,
  DocumentTextIcon,
  FilmIcon,
  MusicalNoteIcon,
} from "@heroicons/react/20/solid"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { StarRating } from "@/app/_components/StarRating"
import { ActionList, actionsHeaderStrip } from "@/app/_components/data/ActionList"
import type { ActionDescriptor } from "@/app/_components/data/ActionList"
import { useRowDrag } from "@/app/_components/data/DataTable"
import type { Column } from "@/app/_components/data/DataTable"
import {
  formatDate,
  formatDuration,
  formatRelativeTime,
  getFullTimestamp,
} from "@/app/utils"
import type { Download } from "@/app/types/DownloadsOptions"
import { isRatable, mediaTypeLabel } from "@/app/lib/mediaScope"

const textCell = "text-xs md:text-sm text-text-primary truncate block"
const monoCell = "text-xs md:text-sm text-text-muted font-mono"

/**
 * Bottom-edge playback progress bar, drawn as a background gradient so it works
 * on a <tr> (which can't hold an absolutely positioned child) and on a <div>.
 */
export const rowProgressStyle = (row: Download) => {
  const percentage =
    row.duration && row.playback_position
      ? Math.min((row.playback_position / row.duration) * 100, 100)
      : 0
  if (percentage <= 0) return undefined
  return {
    backgroundImage: `linear-gradient(to right, color-mix(in srgb, var(--matrix-green) 50%, transparent) ${percentage}%, transparent ${percentage}%)`,
    backgroundSize: "100% 2px",
    backgroundPosition: "bottom",
    backgroundRepeat: "no-repeat",
  }
}

/**
 * Timestamp for the mobile meta line. Shows the field currently being sorted on,
 * so the sort chips have a visible effect on each row. Falls back to the download
 * time, which matches the default (unsorted) order.
 */
export const sortMeta = (
  row: Download,
  sortBy: string | null,
): string | null => {
  if (sortBy === "last_accessed") {
    return row.last_accessed
      ? `watched ${formatRelativeTime(row.last_accessed)}`
      : "never watched"
  }
  if (sortBy === "release_timestamp") {
    return row.release_timestamp
      ? `released ${formatDate(row.release_timestamp)}`
      : null
  }
  return row.downloaded_at
    ? `added ${formatRelativeTime(row.downloaded_at)}`
    : null
}

/** A relative timestamp cell with the full timestamp on hover. */
const timestampCell = (value: string | undefined) => (
  <span
    className={`${monoCell} cursor-help`}
    title={value ? getFullTimestamp(value) : undefined}
  >
    {value ? formatRelativeTime(value) : "-"}
  </span>
)

const positionValue = (position: number) => (
  <span className="text-xs md:text-sm text-text-muted font-mono">{position}</span>
)

/**
 * The ordinal, which becomes a drag handle on row hover.
 *
 * Rather than a handle column of its own: the number is already what you look
 * at when you want to change the order, and a second column would widen the
 * table for the one surface that reorders.
 */
function DraggablePositionCell({ position }: { position: number }) {
  const drag = useRowDrag()
  // Null when the row isn't inside a drag context at all; `disabled` when the
  // list is sorted by something other than position, where reordering has no
  // meaning. Both render the plain number, so the column never changes width.
  const canDrag = drag !== null && !drag.disabled

  return (
    <span
      className="relative inline-flex h-5 w-6 items-center justify-center"
      title={drag?.disabled ? "Sort by # to reorder" : undefined}
    >
      <span
        className={cn(
          "text-xs md:text-sm text-text-muted font-mono transition-opacity",
          canDrag && "[@media(hover:hover)]:group-hover:opacity-0",
        )}
      >
        {position}
      </span>
      {canDrag && (
        <button
          {...drag.attributes}
          // dnd-kit types its listener map as Record<string, Function>.
          onKeyDown={
            drag.listeners?.onKeyDown as
              | React.KeyboardEventHandler<HTMLButtonElement>
              | undefined
          }
          onClick={(e) => e.stopPropagation()}
          title="Drag to reorder"
          aria-label={`Reorder track ${position}`}
          // Hover capability rather than width, following CardActionOverlay:
          // on a touch device this button would never become visible, but it
          // would still sit on top of the cell swallowing taps.
          className={cn(
            "absolute inset-0 hidden items-center justify-center rounded",
            "opacity-0 transition-opacity group-hover:opacity-100",
            "text-text-muted hover:text-matrix cursor-grab active:cursor-grabbing",
            "focus-visible:opacity-100 focus-visible:outline-hidden",
            "focus-visible:ring-1 focus-visible:ring-matrix",
            "[@media(hover:hover)]:flex",
          )}
        >
          <Bars2Icon className="h-4 w-4" />
        </button>
      )}
    </span>
  )
}

/**
 * Ordinal column for ordered media lists (a playlist, a tag mix). Shared so the
 * two surfaces can't drift on width or alignment.
 *
 * `draggable` opts into the handle; it needs the row to be inside a DataTable
 * with `dragAndDrop` set, and is ignored on mobile, where the whole card is the
 * drag target and a handle would just cover the number.
 */
export function positionColumn<T extends { position: number }>(
  options: { draggable?: boolean } = {},
): Column<T> {
  return {
    key: "position",
    label: "#",
    sortable: true,
    mobile: "leading",
    thClassName: "w-12",
    tdClassName: "w-12",
    renderMobile: (row) => positionValue(row.position),
    render: options.draggable
      ? (row) => <DraggablePositionCell position={row.position} />
      : (row) => positionValue(row.position),
  }
}

export type BuildDownloadColumnsArgs = {
  status: string
  actions: ActionDescriptor<Download>[]
  /** Omit to show ratings read-only. */
  onRate?: (mediaId: number, rating: number | null) => void
}

export function buildDownloadColumns({
  status,
  actions,
  onRate,
}: BuildDownloadColumnsArgs): Column<Download>[] {
  return [
    {
      key: "channel",
      label: "Channel",
      mobile: "meta",
      mobileOrder: 2,
      renderMobile: (row) => row.channel,
      tdClassName: "max-w-[140px] md:max-w-[160px] lg:max-w-[200px]",
      render: (row) => (
        <span className={textCell} title={row.channel}>
          {row.channel}
        </span>
      ),
    },
    {
      key: "title",
      label: "Title",
      mobile: "title",
      renderMobile: (row) => row.title,
      tdClassName:
        "max-w-[250px] md:w-full md:max-w-0 lg:w-auto lg:max-w-[400px]",
      render: (row) => (
        <span className={textCell} title={row.title}>
          {row.title.length > 80 ? row.title.slice(0, 80) + "..." : row.title}
        </span>
      ),
    },
    {
      key: "media_type",
      label: "Type",
      breakpoint: "md",
      // On mobile the type is an icon rather than a text badge — it sits inline
      // with the meta line, where a pill would dominate.
      mobile: "badge",
      renderMobile: (row) => {
        const TypeIcon =
          row.media_type === "VIDEO"
            ? FilmIcon
            : row.media_type === "TRANSCRIPT"
              ? DocumentTextIcon
              : MusicalNoteIcon
        return <TypeIcon className="h-3.5 w-3.5 shrink-0 text-text-muted" />
      },
      render: (row) => (
        <Badge variant="outline" className="text-xs font-mono">
          {mediaTypeLabel(status, row)}
        </Badge>
      ),
    },
    {
      key: "duration",
      label: "Duration",
      sortable: true,
      breakpoint: "md",
      mobile: "meta",
      mobileOrder: 1,
      renderMobile: (row) => formatDuration(row.duration),
      render: (row) => (
        <span className={monoCell}>{formatDuration(row.duration)}</span>
      ),
    },
    {
      key: "rating",
      label: "Rating",
      sortable: true,
      breakpoint: "lg",
      stopRowClick: true,
      mobile: "hidden",
      render: (row) =>
        isRatable(status, row) ? (
          <StarRating
            rating={row.rating}
            onRate={onRate && ((r) => onRate(row.media_details_id, r))}
            compact
          />
        ) : null,
    },
    {
      key: "release_timestamp",
      label: "Released",
      sortable: true,
      breakpoint: "lg",
      mobile: "hidden",
      render: (row) => (
        <span className={monoCell}>{formatDate(row.release_timestamp)}</span>
      ),
    },
    {
      key: "downloaded_at",
      label: "Downloaded",
      sortable: true,
      breakpoint: "lg",
      mobile: "hidden",
      render: (row) => timestampCell(row.downloaded_at),
    },
    {
      key: "last_accessed",
      label: "Last Watched",
      sortable: true,
      breakpoint: "lg",
      mobile: "hidden",
      render: (row) => timestampCell(row.last_accessed),
    },
    {
      key: "actions",
      label: actionsHeaderStrip(actions),
      stopRowClick: true,
      mobile: "hidden",
      render: (row) => (
        <div className="flex items-center gap-1 [&_svg]:h-4 [&_svg]:w-4">
          <ActionList actions={actions} row={row} />
        </div>
      ),
    },
  ]
}
