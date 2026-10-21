"use client"

import { useCallback, useRef, useState } from "react"
import axios from "axios"
import toast from "react-hot-toast"
import { apiUrl, errorMessage } from "@/app/lib/api"
import { mediaApi } from "@/app/lib/mediaApi"
import { useFetchEffect } from "@/app/_hooks/useFetchEffect"
import { useStoredValue, writeStored } from "@/app/_hooks/useStoredValue"
import { requestTranscript } from "@/app/_hooks/useMediaActions"
import { useAuth } from "@/app/context/AuthContext"
import { useOffline } from "@/app/context/OfflineContext"
import {
  isTranscribing,
  transcriptButtonState,
  type TranscriptButtonState,
  type TranscriptDocument,
} from "@/app/_components/transcript/transcript"

const OPEN_KEY = "showTranscript"
const POLL_MS = 5000

/** Turns the panel on, for an entry point that came looking for a transcript. */
export function showTranscriptPanel() {
  writeStored(OPEN_KEY, "1")
}

/**
 * Whether the panel shows, remembered across items so it stays open while browsing.
 * Separate from `useMediaTranscript` so a toggle re-renders only the page frame and its
 * button, not the host that loaded the transcript (DownloadsCard among them).
 */
export function useTranscriptOpen(): [boolean, (open: boolean) => void] {
  const open = useStoredValue(() => localStorage.getItem(OPEN_KEY) === "1", false)
  const setOpen = useCallback((next: boolean) => writeStored(OPEN_KEY, next ? "1" : "0"), [])
  return [open, setOpen]
}

export type MediaTranscript = {
  doc: TranscriptDocument | null
  /** The item couldn't be loaded (gone, or no longer shared with this user). */
  failed: boolean
  buttonState: TranscriptButtonState
  generate: () => void
  /** Optimistic edits to the loaded item, such as a rating. */
  patchMedia: (patch: Partial<TranscriptDocument["media"]>) => void
}

/**
 * One item's transcript and the state of its transcription, for a media page.
 *
 * Polls while a job runs, so the button's progress moves and the panel opens itself the
 * moment a job this page started (or found running) delivers. `mediaId` null loads
 * nothing; so does offline mode, which hides every server-touching control.
 */
export function useMediaTranscript(mediaId: number | null): MediaTranscript {
  const { user } = useAuth()
  const { offlineMode } = useOffline()
  const [loaded, setLoaded] = useState<{ id: number; doc: TranscriptDocument } | null>(null)
  const [failedId, setFailedId] = useState<number | null>(null)
  const doc = loaded !== null && loaded.id === mediaId ? loaded.doc : null
  // Which item had a job running at the last load. A ref: only the next load reads it.
  const watchedJob = useRef<number | null>(null)

  useFetchEffect(
    (signal) => {
      if (mediaId === null) return
      return axios
        .get<TranscriptDocument>(apiUrl(mediaApi.transcript(mediaId)), { signal })
        .then(({ data }) => {
          if (watchedJob.current === mediaId && data.blocks.length > 0) showTranscriptPanel()
          watchedJob.current = isTranscribing(data) ? mediaId : null
          setLoaded({ id: mediaId, doc: data })
        })
        .catch((error) => {
          if (axios.isCancel(error)) return
          console.error("Failed to load the transcript", error)
          setFailedId(mediaId)
        })
    },
    [mediaId],
    { enabled: mediaId !== null && !offlineMode, pollMs: isTranscribing(doc) ? POLL_MS : null }
  )

  const patchMedia = useCallback(
    (patch: Partial<TranscriptDocument["media"]>) =>
      setLoaded((current) =>
        current && current.id === mediaId
          ? { ...current, doc: { ...current.doc, media: { ...current.doc.media, ...patch } } }
          : current
      ),
    [mediaId]
  )

  const generate = useCallback(() => {
    if (mediaId === null) return
    requestTranscript(mediaId)
      .then(() => {
        toast.success("Transcript job started")
        watchedJob.current = mediaId
        patchMedia({ transcript_task_status: "QUEUED", transcript_task_progress: 0 })
      })
      .catch((error) => toast.error(errorMessage(error, "Failed to start the transcript job")))
  }, [mediaId, patchMedia])

  return {
    doc,
    failed: doc === null && failedId !== null && failedId === mediaId,
    buttonState: transcriptButtonState(doc, user, offlineMode),
    generate,
    patchMedia,
  }
}
