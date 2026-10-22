"use client"

import { useState } from "react"
import axios from "axios"
import toast from "react-hot-toast"
import {
  TrashIcon,
  ArrowTopRightOnSquareIcon,
} from "@heroicons/react/20/solid"
import { TrashIcon as TrashOutlineIcon } from "@heroicons/react/24/outline"

import { Badge } from "@/components/ui/badge"
import { DataTable } from "./data/DataTable"
import type { Column } from "./data/DataTable"
import { ActionList } from "./data/ActionList"
import type { ActionDescriptor } from "./data/ActionList"
import { ConfirmDialog, ConfirmDetailGrid, getBasename } from "./ConfirmDialog"
import { formatDuration, formatRelativeTime, getFullTimestamp } from "@/app/utils"
import { Clip, SortDirection } from "@/app/types/ClipsOptions"
import { useMediaPlayer } from "@/app/context/MediaPlayerContext"
import { apiUrl } from "@/app/lib/api"
import { sourceUrlAt } from "@/app/_components/transcript/transcript"

/** Sort fields offered on mobile, where column headers aren't available. */
const CLIP_SORT_OPTIONS = [{ key: "created_at", label: "Created" }]

type ClipsTableProps = {
  tableRows: Clip[]
  loading: boolean
  refreshClips: () => void
  sortBy: string | null
  sortDirection: SortDirection
  onSort: (column: string) => void
  onVideoClipClick: (clip: Clip) => void
  onJumpToSourceVideo: (clip: Clip) => void
  selectedIds?: Set<number>
  onSelectionChange?: (ids: Set<number>) => void
  allSelected?: boolean
  onSelectAll?: (selected: boolean) => void
}

export function ClipsTable({
  tableRows,
  loading,
  refreshClips,
  sortBy,
  sortDirection,
  onSort,
  onVideoClipClick,
  onJumpToSourceVideo,
  selectedIds,
  onSelectionChange,
  allSelected,
  onSelectAll,
}: ClipsTableProps) {
  const [focusItem, setFocusItem] = useState<Clip | null>(null)
  const { openAudioPlayer, closeAudioPlayer } = useMediaPlayer()
  const [playingClipId, setPlayingClipId] = useState<number | null>(null)

  const selectionEnabled =
    selectedIds !== undefined && onSelectionChange !== undefined

  const handleRowClick = (clip: Clip) => {
    if (clip.status !== "COMPLETE" || !clip.file_path) {
      toast.error("Clip is not ready for playback")
      return
    }

    if (clip.media_type === "VIDEO") {
      onVideoClipClick(clip)
    } else if (clip.media_type === "AUDIO") {
      closeAudioPlayer()
      openAudioPlayer({
        media_details_id: clip.id,
        title: clip.title,
        channel: clip.source_channel || "",
        start_time: 0,
        duration: clip.duration,
        visible: true,
        isClip: true,
      })
      setPlayingClipId(clip.id)
    }
  }

  const handleJumpToSource = (clip: Clip) => {
    if (!clip.media_details_id) return

    // A source with no file to play (cut from YouTube, or deleted since) opens where it came from.
    if (clip.source_playable === false) {
      if (clip.source_url) {
        window.open(sourceUrlAt(clip.source_url, clip.start_time), "_blank", "noopener,noreferrer")
      } else {
        toast.error("The source of this clip can't be played")
      }
      return
    }

    if (clip.media_type === "VIDEO") {
      onJumpToSourceVideo(clip)
    } else if (clip.media_type === "AUDIO") {
      closeAudioPlayer()
      openAudioPlayer({
        media_details_id: clip.media_details_id,
        title: clip.source_title || clip.title,
        channel: clip.source_channel || "",
        start_time: clip.start_time,
        duration: undefined,
        visible: true,
        isClip: false,
      })
    }
  }

  const deleteClip = async (id: number) => {
    try {
      const response = await axios.delete(apiUrl(`/clips/${id}`))
      if (response.status === 204) {
        toast.success("Deleted clip")
        refreshClips()
      }
    } catch {
      toast.error("Failed to delete clip")
    }
  }

  const actions: ActionDescriptor<Clip>[] = [
    {
      key: "delete",
      title: "Delete",
      icon: TrashIcon,
      onClick: setFocusItem,
      buttonClassName: "hover:bg-status-error/20",
      iconClassName: "text-text-muted hover:text-status-error",
    },
  ]

  const columns: Column<Clip>[] = [
    {
      key: "title",
      label: "Title",
      mobile: "title",
      renderMobile: (clip) => clip.title,
      tdClassName: "max-w-[200px]",
      render: (clip) => (
        <span
          className="text-xs md:text-sm text-text-primary truncate block"
          title={clip.title}
        >
          {clip.title.length > 40 ? clip.title.slice(0, 40) + "..." : clip.title}
        </span>
      ),
    },
    {
      key: "source_title",
      label: "Source",
      breakpoint: "md",
      mobile: "meta",
      mobileOrder: 2,
      renderMobile: (clip) => clip.source_title || null,
      tdClassName: "max-w-[200px]",
      render: (clip) => (
        <span
          className="text-xs md:text-sm text-text-secondary truncate block"
          title={clip.source_title || ""}
        >
          {clip.source_title
            ? clip.source_title.length > 30
              ? clip.source_title.slice(0, 30) + "..."
              : clip.source_title
            : "-"}
        </span>
      ),
    },
    {
      key: "media_type",
      label: "Type",
      breakpoint: "md",
      mobile: "badge",
      renderMobile: (clip) => (
        <Badge variant="outline" className="text-[10px] font-mono">
          {clip.media_type}
        </Badge>
      ),
      render: (clip) => (
        <Badge variant="outline" className="text-xs font-mono">
          {clip.media_type}
        </Badge>
      ),
    },
    {
      key: "duration",
      label: "Duration",
      mobile: "meta",
      mobileOrder: 1,
      renderMobile: (clip) => formatDuration(clip.duration),
      render: (clip) => (
        <span className="text-xs md:text-sm text-text-muted font-mono">
          {formatDuration(clip.duration)}
        </span>
      ),
    },
    {
      key: "created_at",
      label: "Created",
      sortable: true,
      breakpoint: "lg",
      mobile: "hidden",
      render: (clip) => (
        <span
          className="text-xs md:text-sm text-text-muted font-mono cursor-help"
          title={getFullTimestamp(clip.created_at)}
        >
          {formatRelativeTime(clip.created_at)}
        </span>
      ),
    },
    {
      key: "jump_to_source",
      label: "Jump To",
      breakpoint: "lg",
      stopRowClick: true,
      mobile: "hidden",
      render: (clip) =>
        clip.media_details_id ? (
          <button
            onClick={() => handleJumpToSource(clip)}
            className="flex items-center gap-1 text-xs md:text-sm text-text-secondary hover:text-matrix font-mono transition-colors"
            title={`Jump to source at ${formatDuration(clip.start_time)}`}
          >
            <ArrowTopRightOnSquareIcon className="h-3.5 w-3.5" />
            {formatDuration(clip.start_time)}
          </button>
        ) : (
          <span
            className="text-xs md:text-sm text-text-muted font-mono"
            title="Source no longer available"
          >
            -
          </span>
        ),
    },
    {
      key: "actions",
      label: "",
      stopRowClick: true,
      mobile: "hidden",
      render: (clip) => (
        <div className="flex items-center gap-1 [&_svg]:h-4 [&_svg]:w-4">
          <ActionList actions={actions} row={clip} />
        </div>
      ),
    },
  ]

  return (
    <>
      <DataTable
        columns={columns}
        rows={tableRows}
        loading={loading}
        emptyMessage="No clips found"
        getRowKey={(clip) => clip.id}
        onRowClick={handleRowClick}
        rowClassName={(clip) =>
          playingClipId === clip.id ? "bg-matrix/10" : undefined
        }
        sortBy={sortBy}
        sortDirection={sortDirection}
        onSort={onSort}
        sortOptions={CLIP_SORT_OPTIONS}
        renderActions={(clip) => <ActionList actions={actions} row={clip} />}
        {...(selectionEnabled && {
          selection: {
            selectedIds: selectedIds!,
            onSelectionChange: onSelectionChange!,
            allSelected: allSelected ?? false,
            onSelectAll: onSelectAll ?? (() => {}),
            idOf: (clip: Clip) => clip.id,
          },
        })}
      />

      {focusItem && (
        <ConfirmDialog
          open
          onOpenChange={(open) => {
            if (!open) setFocusItem(null)
          }}
          icon={<TrashOutlineIcon className="h-5 w-5 text-status-error" />}
          title="Confirm Delete"
          description="Are you sure you want to delete this item? This action cannot be undone."
          descriptionClassName="text-status-error/80"
          confirmLabel="Delete"
          onConfirm={() => {
            deleteClip(focusItem.id)
            setFocusItem(null)
          }}
          onCancel={() => setFocusItem(null)}
        >
          <div className="py-4">
            <ConfirmDetailGrid
              rows={[
                {
                  label: "Title:",
                  value: focusItem.title,
                  valueClassName: "text-text-primary truncate",
                },
                { label: "Source:", value: focusItem.source_title || "Unknown" },
                { label: "Type:", value: focusItem.media_type },
                {
                  label: "File:",
                  value: getBasename(focusItem.file_path),
                  valueClassName: "text-text-primary truncate",
                },
              ]}
            />
          </div>
        </ConfirmDialog>
      )}
    </>
  )
}
