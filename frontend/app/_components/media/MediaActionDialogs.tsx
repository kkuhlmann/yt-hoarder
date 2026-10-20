"use client"

import { useCallback, useState } from "react"
import { TrashIcon as TrashOutlineIcon } from "@heroicons/react/24/outline"

import {
  ConfirmDialog,
  ConfirmDetailGrid,
  KeepTranscriptsCheckbox,
  getBasename,
} from "@/app/_components/ConfirmDialog"
import { AddToPlaylistDialog } from "@/app/_components/AddToPlaylistDialog"
import { ShareDialog } from "@/app/_components/ShareDialog"
import { TagsDialog } from "@/app/_components/TagsDialog"
import type { MediaActions } from "@/app/_hooks/useMediaActions"
import type { Download, TagInfo } from "@/app/types/DownloadsOptions"

export type MediaDialogKind =
  | "delete"
  | "deleteTranscripts"
  | "addToPlaylist"
  | "share"
  | "tags"

export type MediaDialogsState = {
  kind: MediaDialogKind | null
  focusItem: Download | null
  keepTranscripts: boolean
  setKeepTranscripts: (keep: boolean) => void
  close: () => void
}

export type MediaDialogs = MediaDialogsState & {
  open: (kind: MediaDialogKind, row: Download) => void
}

/**
 * Which media dialog is open, and for which row.
 *
 * Replaces five independent booleans plus a shared `focusItem` — a shape that
 * existed in duplicate and could in principle represent two open dialogs at
 * once, though no code path ever did.
 */
export function useMediaDialogs(): MediaDialogs {
  const [kind, setKind] = useState<MediaDialogKind | null>(null)
  const [focusItem, setFocusItem] = useState<Download | null>(null)
  const [keepTranscripts, setKeepTranscripts] = useState(true)

  const open = useCallback((nextKind: MediaDialogKind, row: Download) => {
    setFocusItem(row)
    // The delete dialog's checkbox defaults to "keep" on every open, rather
    // than remembering the last choice.
    if (nextKind === "delete") setKeepTranscripts(true)
    setKind(nextKind)
  }, [])

  const close = useCallback(() => {
    setKind(null)
    setFocusItem(null)
  }, [])

  return {
    kind,
    focusItem,
    keepTranscripts,
    setKeepTranscripts,
    open,
    close,
  }
}

type MediaActionDialogsProps = {
  dialogs: MediaDialogsState
  actions: MediaActions
  allTags?: TagInfo[]
}

export function MediaActionDialogs({
  dialogs,
  actions,
  allTags = [],
}: MediaActionDialogsProps) {
  const { kind, focusItem, keepTranscripts, setKeepTranscripts, close } = dialogs
  if (!focusItem) return null
  const transcriptOnly = focusItem.media_type === "TRANSCRIPT"

  return (
    <>
      {kind === "delete" && (
        <ConfirmDialog
          open
          onOpenChange={(open) => {
            if (!open) close()
          }}
          icon={<TrashOutlineIcon className="h-5 w-5 text-status-error" />}
          title="Confirm Delete"
          description="Are you sure you want to delete this item? This action cannot be undone."
          descriptionClassName="text-status-error/80"
          confirmLabel="Delete"
          onConfirm={() => {
            actions.deleteMedia(focusItem.media_details_id, keepTranscripts)
            close()
          }}
          onCancel={close}
        >
          <div className="py-4">
            <ConfirmDetailGrid
              rows={[
                {
                  label: "Title:",
                  value: focusItem.title,
                  valueClassName: "text-text-primary truncate",
                },
                { label: "Channel:", value: focusItem.channel },
                { label: "Type:", value: focusItem.media_type },
                ...(transcriptOnly
                  ? []
                  : [
                      {
                        label: "File:",
                        value: getBasename(focusItem.file_path),
                        valueClassName: "text-text-primary truncate",
                      },
                    ]),
              ]}
            />
          </div>
          {/* The transcript is all a transcript-only item is; the server drops it too. */}
          {!transcriptOnly && (
            <KeepTranscriptsCheckbox
              id="keep-transcripts"
              checked={keepTranscripts}
              onChange={setKeepTranscripts}
            />
          )}
        </ConfirmDialog>
      )}

      {kind === "deleteTranscripts" && (
        <ConfirmDialog
          open
          onOpenChange={(open) => {
            if (!open) close()
          }}
          icon={<TrashOutlineIcon className="h-5 w-5 text-status-error" />}
          title="Confirm Delete"
          description={
            focusItem.transcript_block_count
              ? `Are you sure you want to delete ${focusItem.transcript_block_count} transcript blocks for this item? This action cannot be undone.`
              : `Are you sure you want to delete the transcripts for this item? This action cannot be undone.`
          }
          descriptionClassName="text-status-error/80"
          confirmLabel="Delete"
          onConfirm={() => {
            actions.deleteTranscripts(focusItem.media_details_id)
            close()
          }}
          onCancel={close}
        />
      )}

      {kind === "addToPlaylist" && (
        <AddToPlaylistDialog
          open
          onOpenChange={(open) => {
            if (!open) close()
          }}
          mediaDetailsIds={[focusItem.media_details_id]}
          mediaTitle={focusItem.title}
        />
      )}

      {kind === "share" && (
        <ShareDialog
          open
          onOpenChange={(open) => {
            if (!open) close()
          }}
          entityIds={[focusItem.media_details_id]}
          entityType="media-details"
          entityTitle={focusItem.title}
        />
      )}

      {kind === "tags" && (
        <TagsDialog
          open
          onOpenChange={(open) => {
            if (!open) close()
          }}
          mediaTitle={focusItem.title}
          tags={focusItem.tags || []}
          allTags={allTags}
          onSave={(tagNames) =>
            actions.setTags(focusItem.media_details_id, tagNames)
          }
        />
      )}
    </>
  )
}
