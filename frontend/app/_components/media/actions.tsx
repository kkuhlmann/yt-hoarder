"use client"

import {
  TrashIcon,
  CheckIcon,
  BookOpenIcon,
  XMarkIcon,
  ClockIcon,
  NoSymbolIcon,
  ScissorsIcon,
  PlayCircleIcon,
  UserPlusIcon,
  TagIcon,
  DocumentTextIcon,
  ArrowTopRightOnSquareIcon,
} from "@heroicons/react/20/solid"
import { ArrowDownTrayIcon } from "@heroicons/react/24/outline"
import toast from "react-hot-toast"

import { OfflineDownloadButton } from "./OfflineDownloadButton"
import type { ActionDescriptor } from "@/app/_components/data/ActionList"
import type { MediaActions } from "@/app/_hooks/useMediaActions"
import type { MediaDialogs } from "./MediaActionDialogs"
import type { Download } from "@/app/types/DownloadsOptions"
import { TRANSCRIPTS_SCOPE } from "@/app/lib/mediaScope"

type CurrentUser = { id?: number; is_admin?: boolean } | null | undefined

/**
 * Transcript state as a single control: shows progress, and doubles as the
 * cancel/delete button for whoever owns the media.
 *
 * Icons carry no size class — the containing action strip sets it, so the
 * in-progress ring (an inline <svg>) is sized to match the surrounding icons
 * automatically in both the table cell and the card overlay.
 */
export function TranscriptStatus({
  row,
  user,
  onCancel,
  onGenerate,
}: {
  row: Download
  user: CurrentUser
  onCancel: (mediaId: number) => void
  onGenerate: (mediaId: number) => void
  compact?: boolean
}) {
  const status = row.transcript_task_status
  const progress = row.transcript_task_progress
  const isOwnerOrAdmin = row.owner_id === user?.id || user?.is_admin

  const cancel = (e: React.MouseEvent) => {
    e.stopPropagation()
    onCancel(row.media_details_id)
  }

  const regenerate = (e: React.MouseEvent) => {
    e.stopPropagation()
    onGenerate(row.media_details_id)
  }

  if (status === "QUEUED") {
    if (isOwnerOrAdmin) {
      return (
        <button
          onClick={cancel}
          className="group/trans p-1 rounded hover:bg-status-warning/20 transition-colors inline-flex"
          title="Cancel queued transcript"
        >
          <ClockIcon className="text-status-queued group-hover/trans:hidden" />
          <XMarkIcon className="text-status-warning hidden group-hover/trans:inline" />
        </button>
      )
    }
    return (
      <span className="p-1 inline-flex" title="Queued">
        <ClockIcon className="text-status-queued" />
      </span>
    )
  }

  if (status === "IN_PROGRESS") {
    const pct = Math.min(100, Math.max(0, Math.floor((progress ?? 0) as number)))
    const ringRadius = 9
    const ringCircumference = 2 * Math.PI * ringRadius
    // Inline <svg> with no size class → the action strip sizes it to match the
    // sibling icons (16px table / 14px grid). The arc fills with progress.
    const ring = (
      <svg
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
        className="text-status-info"
      >
        <circle
          cx="12"
          cy="12"
          r={ringRadius}
          strokeWidth="3"
          className="stroke-current opacity-20"
        />
        <circle
          cx="12"
          cy="12"
          r={ringRadius}
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray={ringCircumference}
          strokeDashoffset={ringCircumference * (1 - pct / 100)}
          transform="rotate(-90 12 12)"
          className="stroke-current transition-[stroke-dashoffset] duration-300"
        />
      </svg>
    )
    if (isOwnerOrAdmin) {
      return (
        <button
          onClick={cancel}
          className="group/trans relative p-1 rounded hover:bg-status-warning/20 transition-colors inline-flex items-center"
          title={`Cancel transcript (${pct}%)`}
        >
          {/* Ring stays in flow (invisible, not hidden) so the button width is
              constant when the cancel X overlays it on hover. */}
          <span className="inline-flex group-hover/trans:invisible">{ring}</span>
          <span className="absolute inset-0 hidden items-center justify-center group-hover/trans:flex">
            <XMarkIcon className="text-status-warning" />
          </span>
        </button>
      )
    }
    return (
      <span
        className="p-1 inline-flex items-center"
        title={`Transcribing… ${pct}%`}
      >
        {ring}
      </span>
    )
  }

  if (status === "COMPLETE") {
    if (isOwnerOrAdmin) {
      return (
        <button
          onClick={cancel}
          className="group/trans p-1 rounded hover:bg-status-warning/20 transition-colors inline-flex"
          title="Delete transcript"
        >
          <CheckIcon className="text-matrix group-hover/trans:hidden" />
          <XMarkIcon className="text-status-warning hidden group-hover/trans:inline" />
        </button>
      )
    }
    return (
      <span className="p-1 inline-flex" title="Complete">
        <CheckIcon className="text-matrix" />
      </span>
    )
  }

  if (status === "RETRY") {
    return (
      <span className="p-1 inline-flex" title="Retry">
        <XMarkIcon className="text-status-warning" />
      </span>
    )
  }

  if (status === "FAILED" || status === "CANCELLED") {
    return (
      <button
        onClick={regenerate}
        className="p-1 rounded hover:bg-bg-surface transition-colors"
        title="Retry transcript"
      >
        {status === "FAILED" ? (
          <XMarkIcon className="text-status-error" />
        ) : (
          <NoSymbolIcon className="text-text-muted" />
        )}
      </button>
    )
  }

  return (
    <button
      onClick={regenerate}
      className="p-1 rounded hover:bg-bg-surface transition-colors"
      title="Generate transcript"
    >
      <BookOpenIcon className="text-text-secondary hover:text-matrix" />
    </button>
  )
}

export type BuildMediaActionsArgs = {
  status: string
  user: CurrentUser
  actions: MediaActions
  dialogs: MediaDialogs
  /** Opens the clipping UI for this row. Omit to hide the clip action. */
  onClip?: (row: Download) => void
  /** SKIPPED-only: fills the download form from this row. */
  onPopulateSkipped?: (row: Download) => void
  /** Transcripts view: opens the transcript viewer. Omit to hide the action. */
  onViewTranscript?: (row: Download) => void
  /** True in the grid/card overlay, where text sits tighter. */
  compact?: boolean
  /** True when the app is showing its downloaded library. */
  offlineMode?: boolean
}

const tagsAction = (dialogs: MediaDialogs): ActionDescriptor<Download> => ({
  key: "tags",
  title: "Edit tags",
  icon: TagIcon,
  onClick: (row) => dialogs.open("tags", row),
  buttonClassName: "hover:bg-matrix/20",
  iconClassName: "text-text-muted hover:text-matrix",
})

const shareAction = (user: CurrentUser, dialogs: MediaDialogs): ActionDescriptor<Download> => ({
  key: "share",
  title: "Share",
  icon: UserPlusIcon,
  onClick: (row) => {
    if (row.owner_id !== user?.id && !user?.is_admin) {
      toast.error("Only the owner can manage sharing")
      return
    }
    dialogs.open("share", row)
  },
  buttonClassName: "hover:bg-matrix/20",
  iconClassName: "text-text-muted hover:text-matrix",
})

/**
 * The Transcripts view mixes transcript-only items, library media and deleted media
 * whose transcript was kept, under one shared column of actions — so each action
 * decides per row rather than per scope. Playback, clips, playlists and offline stay
 * in the Library, where every row has a file.
 */
function transcriptsViewActions(
  user: CurrentUser,
  dialogs: MediaDialogs,
  onViewTranscript: ((row: Download) => void) | undefined
): ActionDescriptor<Download>[] {
  // A soft delete removed every share and tag, so neither applies to a deleted row.
  const deleted = (row: Download) => row.status === "DELETED"
  const viewTranscript: ActionDescriptor<Download>[] = onViewTranscript
    ? [
        {
          key: "viewTranscript",
          title: "View transcript",
          icon: DocumentTextIcon,
          onClick: onViewTranscript,
          buttonClassName: "hover:bg-matrix/20",
          iconClassName: "text-text-muted hover:text-matrix",
        },
      ]
    : []
  return [
    ...viewTranscript,
    {
      key: "source",
      title: "Open source",
      icon: ArrowTopRightOnSquareIcon,
      onClick: (row) => {
        if (row.url) window.open(row.url, "_blank", "noopener,noreferrer")
      },
      buttonClassName: "hover:bg-matrix/20",
      iconClassName: "text-text-muted hover:text-matrix",
    },
    { ...tagsAction(dialogs), disabled: deleted, disabledTitle: "Deleted media has no tags" },
    {
      ...shareAction(user, dialogs),
      disabled: deleted,
      disabledTitle: "Deleted media can't be shared",
    },
    {
      key: "delete",
      title: (row) => (deleted(row) ? "Delete transcript" : "Delete"),
      icon: TrashIcon,
      onClick: (row) => dialogs.open(deleted(row) ? "deleteTranscripts" : "delete", row),
      buttonClassName: "hover:bg-status-error/20",
      iconClassName: "text-text-muted hover:text-status-error",
    },
  ]
}

const OFFLINE_ACTION: ActionDescriptor<Download> = {
  key: "offline",
  title: "Save for offline",
  headerIcon: ArrowDownTrayIcon,
  render: (row) => <OfflineDownloadButton row={row} />,
}

/**
 * Row actions for a media item, as descriptors.
 *
 * Returned as an array so surfaces can extend it by concatenation — the
 * playlist track row is these actions plus move up / move down / remove.
 *
 * Offline mode is playback-only, so it keeps just the offline button (a local
 * remove). Everything else here writes to the server, and offering a button
 * whose only outcome is a failed request reads as a broken app.
 */
export function buildMediaActions({
  status,
  user,
  actions,
  dialogs,
  onClip,
  onPopulateSkipped,
  onViewTranscript,
  compact = false,
  offlineMode = false,
}: BuildMediaActionsArgs): ActionDescriptor<Download>[] {
  if (offlineMode) return [OFFLINE_ACTION]

  if (status === TRANSCRIPTS_SCOPE) {
    return transcriptsViewActions(user, dialogs, onViewTranscript)
  }

  if (status === "SKIPPED") {
    return [
      {
        key: "download",
        title: "Download this media",
        icon: ArrowDownTrayIcon,
        onClick: (row) => onPopulateSkipped?.(row),
        buttonClassName: "hover:bg-matrix/20",
        iconClassName: "text-text-muted hover:text-matrix",
      },
    ]
  }

  if (status === "DELETED") {
    return [
      {
        key: "transcripts",
        title: "Transcripts",
        icon: BookOpenIcon,
        render: (row) =>
          (row.transcript_block_count ?? 0) > 0 ? (
            <button
              onClick={(e) => {
                e.stopPropagation()
                dialogs.open("deleteTranscripts", row)
              }}
              className="p-1 rounded hover:bg-status-warning/20 transition-colors inline-flex items-center gap-0.5"
              title={`Delete ${row.transcript_block_count} transcript blocks`}
            >
              <BookOpenIcon className="text-matrix hover:text-status-warning" />
              <span
                className={`font-mono text-matrix ${compact ? "text-[10px]" : "text-xs"}`}
              >
                {row.transcript_block_count}
              </span>
            </button>
          ) : (
            <span className="p-1 inline-flex">
              <BookOpenIcon className="text-text-muted/30" />
            </span>
          ),
      },
      {
        key: "hardDelete",
        title: "Permanently delete",
        icon: TrashIcon,
        onClick: (row) => actions.hardDeleteMedia(row.media_details_id),
        buttonClassName: "hover:bg-status-error/20",
        iconClassName: "text-text-muted hover:text-status-error",
      },
    ]
  }

  // COMPLETE
  return [
    {
      key: "transcript",
      title: "Transcript Status",
      headerIcon: BookOpenIcon,
      render: (row) => (
        <TranscriptStatus
          row={row}
          user={user}
          compact={compact}
          onCancel={(id) => actions.deleteTranscripts(id)}
          onGenerate={(id) => actions.generateTranscript(id)}
        />
      ),
    },
    tagsAction(dialogs),
    {
      key: "playlist",
      title: "Add to playlist",
      icon: PlayCircleIcon,
      onClick: (row) => dialogs.open("addToPlaylist", row),
      buttonClassName: "hover:bg-matrix/20",
      iconClassName: "text-text-muted hover:text-matrix",
    },
    shareAction(user, dialogs),
    OFFLINE_ACTION,
    {
      key: "clip",
      title: "Create clip",
      icon: ScissorsIcon,
      onClick: (row) => onClip?.(row),
      buttonClassName: "hover:bg-matrix/20",
      iconClassName: "text-text-muted hover:text-matrix",
    },
    {
      key: "delete",
      title: "Delete",
      icon: TrashIcon,
      onClick: (row) => dialogs.open("delete", row),
      buttonClassName: "hover:bg-status-error/20",
      iconClassName: "text-text-muted hover:text-status-error",
    },
  ]
}
