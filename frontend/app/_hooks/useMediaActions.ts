"use client"

import { useMemo } from "react"
import axios from "axios"
import toast from "react-hot-toast"

import { apiUrl, errorMessage } from "@/app/lib/api"
import { mediaApi } from "@/app/lib/mediaApi"
import type { Download } from "@/app/types/DownloadsOptions"

export type UseMediaActionsArgs = {
  /**
   * Optimistic patch of one row in whatever list is being displayed.
   *
   * Accepts a generic patch function rather than being tied to a specific type,
   * allowing it to work with both Download[] and PlaylistTrack[] setters.
   */
  patchRow: (mediaDetailsId: number, patch: Partial<Download>) => void
  /** Full refetch — used after destructive actions and to undo failed optimism. */
  onRefresh: () => void
  /** Called after tags change, so a tag filter list upstream can refresh. */
  onTagsChange?: () => void
}

export type MediaActions = {
  rate: (mediaId: number, rating: number | null) => Promise<void>
  setTags: (mediaId: number, tagNames: string[]) => Promise<void>
  generateTranscript: (mediaId: number) => Promise<void>
  deleteTranscripts: (mediaId: number) => Promise<void>
  deleteMedia: (mediaId: number, keepTranscripts?: boolean) => Promise<void>
  hardDeleteMedia: (mediaId: number) => Promise<void>
}

/**
 * Rating transport, shared by the row actions, the details card and the player.
 *
 * Only the request is shared: each caller's optimistic state and rollback differ,
 * so they keep their own.
 */
export const saveRating = (mediaId: number, rating: number | null) =>
  rating === null
    ? axios.delete(apiUrl(mediaApi.rating(mediaId)))
    : axios.put(apiUrl(mediaApi.rating(mediaId)), { rating })

/** Starts a transcription job; the server accepts it from the owner or an admin. */
export const requestTranscript = (mediaId: number) =>
  axios.post(apiUrl(mediaApi.createTranscript(mediaId)))

/** The media row actions, in one place — every list surface shares these. */
export function useMediaActions({
  patchRow,
  onRefresh,
  onTagsChange,
}: UseMediaActionsArgs): MediaActions {
  return useMemo<MediaActions>(
    () => ({
      async rate(mediaId, rating) {
        // Optimistic: stars should respond instantly, and a failed rating is
        // cheap to roll back with a refetch.
        patchRow(mediaId, { rating })
        try {
          await saveRating(mediaId, rating)
        } catch {
          toast.error("Failed to update rating")
          onRefresh()
        }
      },

      async setTags(mediaId, tagNames) {
        try {
          const response = await axios.put(apiUrl(mediaApi.tags(mediaId)), {
            tag_names: tagNames,
          })
          // Not optimistic: the server resolves names to tag ids, so the
          // response is the only source of the real tag list.
          patchRow(mediaId, { tags: response.data })
          onTagsChange?.()
          toast.success("Set tags")
        } catch {
          toast.error("Failed to update tags")
          onRefresh()
        }
      },

      async generateTranscript(mediaId) {
        // Optimistic: the transcript control's icon *is* this status, so it has
        // to flip before the request resolves. Both former copies did this at
        // the call site; doing it here means a caller can't forget it.
        patchRow(mediaId, { transcript_task_status: "QUEUED" })
        try {
          const response = await requestTranscript(mediaId)
          if (response.status === 200) {
            toast.success("Submitted Transcript Job")
            patchRow(mediaId, {
              transcript_task_status: response.data.status,
            })
          }
        } catch {
          // Pre-existing: no rollback, so a failed submit leaves the row
          // showing QUEUED until the next poll corrects it.
          toast.error("An error occurred")
        }
      },

      async deleteTranscripts(mediaId) {
        try {
          const response = await axios.delete(
            apiUrl(mediaApi.transcripts(mediaId)),
          )
          if (response.status === 200) {
            toast.success(
              response.data?.task_cancelled
                ? "Transcript task cancelled"
                : "Transcripts deleted",
            )
            onRefresh()
          }
        } catch {
          toast.error("An error occurred")
        }
      },

      async deleteMedia(mediaId, keepTranscripts = false) {
        try {
          const response = await axios.delete(apiUrl(mediaApi.detail(mediaId)), {
            params: { keep_transcripts: keepTranscripts },
          })
          if (response.status === 204) {
            toast.success(
              keepTranscripts
                ? "Deleted Download (transcripts kept)"
                : "Deleted Download",
            )
            onRefresh()
          }
        } catch (error) {
          toast.error(errorMessage(error))
        }
      },

      async hardDeleteMedia(mediaId) {
        try {
          const response = await axios.delete(
            apiUrl(mediaApi.hardDelete(mediaId)),
          )
          if (response.status === 204) {
            toast.success("Permanently deleted")
            onRefresh()
          }
        } catch (error) {
          toast.error(errorMessage(error))
        }
      },
    }),
    [patchRow, onRefresh, onTagsChange],
  )
}
