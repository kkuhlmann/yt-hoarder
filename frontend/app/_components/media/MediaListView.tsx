"use client"

import { useCallback } from "react"

import { DataTable } from "@/app/_components/data/DataTable"
import type {
  Column,
  DataTableDragAndDrop,
  DataTableSelection,
} from "@/app/_components/data/DataTable"
import { CardGrid } from "@/app/_components/data/CardGrid"
import { ActionList } from "@/app/_components/data/ActionList"
import type { ActionDescriptor } from "@/app/_components/data/ActionList"
import { StarRating } from "@/app/_components/StarRating"
import { MediaCard } from "./MediaCard"
import { MediaActionDialogs, useMediaDialogs } from "./MediaActionDialogs"
import { buildMediaActions } from "./actions"
import { buildDownloadColumns, rowProgressStyle, sortMeta } from "./columns"
import { useMediaActions } from "@/app/_hooks/useMediaActions"
import type { ViewMode } from "@/app/_hooks/useViewMode"
import { useAuth } from "@/app/context/AuthContext"
import { useMediaPlayer } from "@/app/context/MediaPlayerContext"
import { useOffline } from "@/app/context/OfflineContext"
import { isRatable, mediaTypeLabel } from "@/app/lib/mediaScope"
import type {
  Download,
  SortDirection,
  TagInfo,
} from "@/app/types/DownloadsOptions"

export type MediaListViewProps<T extends Download> = {
  viewMode: ViewMode
  rows: T[]
  loading: boolean
  /** COMPLETE | TRANSCRIPTS | DELETED | SKIPPED — drives the available actions. */
  status: string
  onRefresh: () => void
  patchRow: (mediaDetailsId: number, patch: Partial<Download>) => void

  sortBy: string | null
  sortDirection: SortDirection
  onSort: (column: string) => void
  sortOptions?: { key: string; label: string }[]

  allTags?: TagInfo[]
  onTagsChange?: () => void

  onRowClick?: (row: T) => void
  rowClassName?: (row: T) => string | undefined
  onClip?: (row: T) => void
  onPopulateSkipped?: (row: T) => void
  /** Transcripts view: opens the transcript viewer for a row. */
  onViewTranscript?: (row: T) => void

  /** Appended after the shared media actions (playlist reorder/remove). */
  extraActions?: ActionDescriptor<T>[]
  /** Prepended before the shared media columns (playlist position). */
  leadingColumns?: Column<T>[]
  selection?: DataTableSelection<T>
  emptyMessage?: string
  /** Table/list only — the grid lays out in two dimensions and ignores this. */
  dragAndDrop?: DataTableDragAndDrop<T>
  /**
   * The saved-position bar under each row. Off for a playlist that doesn't
   * resume, where it would advertise a position playback then ignores.
   */
  showPlaybackProgress?: boolean
}

/**
 * A list of media, as either a table or a card grid.
 *
 * Replaces DownloadsTable and DownloadsGrid, which were two components with two
 * private copies of the same actions and dialogs. Playlist detail renders the
 * same rows by passing `leadingColumns` (position) and `extraActions`
 * (move up / move down / remove).
 */
export function MediaListView<T extends Download>({
  viewMode,
  rows,
  loading,
  status,
  onRefresh,
  patchRow,
  sortBy,
  sortDirection,
  onSort,
  sortOptions,
  allTags = [],
  onTagsChange,
  onRowClick,
  rowClassName,
  onClip,
  onPopulateSkipped,
  onViewTranscript,
  extraActions = [],
  leadingColumns = [],
  selection,
  emptyMessage = "No downloads found",
  dragAndDrop,
  showPlaybackProgress = true,
}: MediaListViewProps<T>) {
  const { user } = useAuth()
  const { savedPositions } = useMediaPlayer()
  const { offlineMode } = useOffline()
  const dialogs = useMediaDialogs()
  const actions = useMediaActions({ patchRow, onRefresh, onTagsChange })

  /**
   * `rows` is a snapshot from whenever the list was fetched, so a track's bar
   * would otherwise sit still no matter how much of it you played. Applied at
   * render rather than patched into `rows`, which would be a write during an
   * effect for a value that is purely derived.
   */
  const withLivePosition = useCallback(
    (row: T): T => {
      const live = savedPositions[row.media_details_id]
      return live === undefined ? row : { ...row, playback_position: live }
    },
    [savedPositions],
  )

  const rateOnline = useCallback(
    (mediaId: number, rating: number | null) => actions.rate(mediaId, rating),
    [actions],
  )
  // Offline mode is playback-only: ratings stay visible (the offline list sorts
  // and filters on them) but a click would be a server write, so no handler.
  const rate = offlineMode ? undefined : rateOnline

  const buildActions = (compact: boolean) =>
    [
      ...buildMediaActions({
        status,
        user,
        actions,
        dialogs,
        compact,
        offlineMode,
        onClip: onClip as ((row: Download) => void) | undefined,
        onPopulateSkipped: onPopulateSkipped as
          | ((row: Download) => void)
          | undefined,
        onViewTranscript: onViewTranscript as ((row: Download) => void) | undefined,
      }),
      ...extraActions,
    ] as ActionDescriptor<T>[]

  const tableActions = buildActions(false)
  const cardActions = buildActions(true)

  const columns: Column<T>[] = [
    ...leadingColumns,
    ...(buildDownloadColumns({
      status,
      actions: tableActions as ActionDescriptor<Download>[],
      onRate: rate,
    }) as Column<T>[]),
  ]

  return (
    <>
      {viewMode === "grid" ? (
        <CardGrid
          rows={rows}
          loading={loading}
          emptyMessage={emptyMessage}
          getRowKey={(row) => row.media_details_id}
          sortBy={sortBy}
          sortDirection={sortDirection}
          onSort={onSort}
          sortOptions={sortOptions}
          renderCard={(row, index) => (
            <MediaCard
              row={withLivePosition(row)}
              index={index}
              className={rowClassName?.(row)}
              onClick={() => onRowClick?.(row)}
              actions={<ActionList actions={cardActions} row={row} />}
              showProgress={showPlaybackProgress}
              typeLabel={mediaTypeLabel(status, row)}
              ratingSlot={
                isRatable(status, row) ? (
                  <StarRating
                    rating={row.rating}
                    onRate={rate && ((r) => rate(row.media_details_id, r))}
                    compact
                  />
                ) : undefined
              }
            />
          )}
        />
      ) : (
        <DataTable
          columns={columns}
          rows={rows}
          loading={loading}
          emptyMessage={emptyMessage}
          getRowKey={(row) => row.media_details_id}
          onRowClick={onRowClick}
          rowClassName={rowClassName}
          rowStyle={
            showPlaybackProgress
              ? (row) => rowProgressStyle(withLivePosition(row))
              : undefined
          }
          sortBy={sortBy}
          sortDirection={sortDirection}
          onSort={onSort}
          sortOptions={sortOptions}
          mobileMeta={(row) => sortMeta(row, sortBy)}
          renderActions={(row) => <ActionList actions={cardActions} row={row} />}
          selection={selection}
          dragAndDrop={dragAndDrop}
        />
      )}

      {!offlineMode && (
        <MediaActionDialogs
          dialogs={dialogs}
          actions={actions}
          allTags={allTags}
        />
      )}
    </>
  )
}
