"use client"

import { useCallback, useEffect, useRef } from "react"
import axios from "axios"
import toast from "react-hot-toast"
import { apiUrl, errorMessage } from "@/app/lib/api"
import { mediaApi } from "@/app/lib/mediaApi"
import { playableRecords } from "@/app/lib/offlineLibrary"
import type { DeepLink } from "@/app/lib/deepLink"
import { useFetchEffect } from "@/app/_hooks/useFetchEffect"

/** The fields of `GET /media-details/{id}` that playback needs. */
export type DeepLinkMedia = {
  id: number
  media_type: string
  title: string
  channel: string
  url: string
  duration: number | null
  thumbnail_path?: string
  status: string
  file_path: string | null
}

type DeepLinkHandlers = {
  onAudio: (media: DeepLinkMedia, seconds: number) => void
  onVideo: (media: DeepLinkMedia, seconds: number) => void
  /** A transcript-only item: no file to play, so the link opens its transcript. */
  onTranscript: (media: DeepLinkMedia, seconds: number) => void
  /** Called once the link has been acted on or found unusable, so a remount won't replay it. */
  onHandled: () => void
}

async function loadMedia(
  mediaId: number,
  offlineMode: boolean,
  signal: AbortSignal
): Promise<DeepLinkMedia | null> {
  if (offlineMode) {
    // The stored record is that same endpoint's response, kept by offlineDownloader.
    const record = (await playableRecords()).find((r) => r.id === mediaId)
    return (record as unknown as DeepLinkMedia | undefined) ?? null
  }
  try {
    const resp = await axios.get<DeepLinkMedia>(apiUrl(mediaApi.detail(mediaId)), { signal })
    return resp.data
  } catch (error) {
    // Missing and not-shared-with-you are the same 404, and the same answer here.
    if (axios.isAxiosError(error) && error.response?.status === 404) return null
    throw error
  }
}

/**
 * Plays the media a `/?media=<id>&t=<seconds>` link points at, once.
 *
 * Lives with DownloadsCard because video only plays in its inline player. The handlers
 * are read through a ref so the caller's per-render closures don't refetch the media.
 */
export function useDeepLinkPlayback(
  deepLink: DeepLink | null,
  offlineMode: boolean,
  handlers: DeepLinkHandlers
) {
  const handlersRef = useRef(handlers)
  useEffect(() => {
    handlersRef.current = handlers
  })

  const play = useCallback(
    async (signal: AbortSignal) => {
      if (!deepLink) return
      try {
        const media = await loadMedia(deepLink.mediaId, offlineMode, signal)
        if (signal.aborted) return
        const { onAudio, onVideo, onTranscript } = handlersRef.current
        if (!media) {
          toast.error(
            offlineMode
              ? "That video isn't downloaded to this device"
              : "That video isn't in your library"
          )
        } else if (media.media_type === "TRANSCRIPT" && media.status === "COMPLETE") {
          onTranscript(media, deepLink.seconds)
        } else if (media.status !== "COMPLETE" || !media.file_path) {
          toast.error("Media file not available")
        } else if (media.media_type === "VIDEO") {
          onVideo(media, deepLink.seconds)
        } else {
          onAudio(media, deepLink.seconds)
        }
      } catch (error) {
        if (signal.aborted || axios.isCancel(error)) return
        toast.error(errorMessage(error, "Could not open the linked video"))
      }
      // Reached only by a run that finished: an aborted one (unmount, StrictMode's
      // double mount) returns above and leaves the link for the run that replaces it.
      handlersRef.current.onHandled()
    },
    [deepLink, offlineMode]
  )

  useFetchEffect(play, [play], { enabled: deepLink !== null })
}
