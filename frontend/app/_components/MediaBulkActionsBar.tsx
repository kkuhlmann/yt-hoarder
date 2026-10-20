"use client"

import { Download } from "@/app/types/DownloadsOptions"
import { BulkBar, BulkBarButton } from "./BulkBar"

type BulkActionType = "delete" | "tag" | "share" | "playlist" | null

type ExtraBulkAction = {
  key: string
  label: string
  loadingLabel: string
  onClick: () => void
  isLoading?: boolean
}

type MediaBulkActionsBarProps = {
  selectedItems: Download[]
  /** The selected rows tags and shares apply to: all but deleted ones. Defaults to all. */
  editableItems?: Download[]
  currentUserId: number | null
  onDelete: () => void
  onTag: () => void
  onShare: () => void
  /** Omit to leave the button out. */
  onPlaylist?: () => void
  onClearSelection: () => void
  loadingAction?: BulkActionType
  /** Surface-specific buttons, e.g. playlist detail's "Remove from playlist". */
  extraActions?: ExtraBulkAction[]
}

export function MediaBulkActionsBar({
  selectedItems,
  editableItems = selectedItems,
  currentUserId,
  onDelete,
  onTag,
  onShare,
  onPlaylist,
  onClearSelection,
  loadingAction = null,
  extraActions = [],
}: MediaBulkActionsBarProps) {
  const totalCount = selectedItems.length
  const editableCount = editableItems.length
  const ownedCount = editableItems.filter((i) => i.owner_id === currentUserId).length

  const buttons: BulkBarButton[] = [
    {
      key: "delete",
      onClick: onDelete,
      variant: "destructive",
      isLoading: loadingAction === "delete",
      loadingLabel: "Deleting...",
      content: <>Delete ({totalCount})</>,
    },
    {
      key: "tag",
      onClick: onTag,
      variant: "secondary",
      disabled: editableCount === 0,
      title: editableCount < totalCount ? "Deleted media has no tags" : undefined,
      isLoading: loadingAction === "tag",
      loadingLabel: "Tagging...",
      content: (
        <>
          Tags
          {editableCount > 0 && editableCount !== totalCount && (
            <span className="text-xs opacity-80">
              ({editableCount} of {totalCount})
            </span>
          )}
        </>
      ),
    },
    ...(onPlaylist
      ? [
          {
            key: "playlist",
            onClick: onPlaylist,
            variant: "secondary" as const,
            isLoading: loadingAction === "playlist",
            loadingLabel: "Adding...",
            content: "Add to Playlist",
          },
        ]
      : []),
    {
      key: "share",
      onClick: onShare,
      variant: "secondary",
      disabled: ownedCount === 0,
      isLoading: loadingAction === "share",
      loadingLabel: "Sharing...",
      content: (
        <>
          Share
          {ownedCount > 0 && ownedCount !== totalCount && (
            <span className="text-xs opacity-80">({ownedCount} owned)</span>
          )}
        </>
      ),
    },
    ...extraActions.map((action) => ({
      key: action.key,
      onClick: action.onClick,
      variant: "secondary" as const,
      isLoading: action.isLoading,
      loadingLabel: action.loadingLabel,
      content: `${action.label} (${totalCount})`,
    })),
  ]

  return (
    <BulkBar
      count={totalCount}
      onClearSelection={onClearSelection}
      isLoading={loadingAction !== null || extraActions.some((a) => a.isLoading)}
      buttons={buttons}
    />
  )
}
