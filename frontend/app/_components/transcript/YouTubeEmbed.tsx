"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useMediaPlayer } from "@/app/context/MediaPlayerContext"
import { savePlaybackPosition } from "@/app/lib/playbackSync"
import { parsePlayerInfo, positionSaveDue, type SeekRef } from "./transcript"

const YOUTUBE_ORIGIN = "https://www.youtube-nocookie.com"

/**
 * The privacy-enhanced embed, driven over postMessage rather than the IFrame API
 * script, which would be the app's only third-party script.
 *
 * The player only reports its position to a page that has announced it is listening,
 * hence the `listening` message on load; until then the transcript follows clicks.
 *
 * Playback here is recorded like the app player's (Last Watched, play count, the row's
 * progress), since for a transcript-only item this embed is the only way to watch it.
 * Only while it is actually playing, so opening the page alone records nothing.
 */
export function YouTubeEmbed({
  mediaId,
  videoId,
  start,
  title,
  onTimeUpdate,
  seekRef,
}: {
  mediaId: number
  videoId: string
  start: number
  title: string
  onTimeUpdate: (seconds: number) => void
  seekRef: SeekRef
}) {
  const frameRef = useRef<HTMLIFrameElement>(null)
  const { notePlaybackPosition } = useMediaPlayer()
  const playingRef = useRef(false)
  const lastSavedRef = useRef<number | null>(null)
  // Fixed at mount: a new src would reload the player, and later seeks go over postMessage.
  const [src] = useState(() => {
    const params = new URLSearchParams({
      start: String(Math.floor(start)),
      autoplay: "1",
      // Without it an iPhone hands the video to its native fullscreen player, which
      // fails inside a home-screen web app; inline is also what keeps the page usable.
      playsinline: "1",
      enablejsapi: "1",
      rel: "0",
      origin: window.location.origin,
    })
    return `${YOUTUBE_ORIGIN}/embed/${encodeURIComponent(videoId)}?${params}`
  })

  const post = useCallback((message: object) => {
    frameRef.current?.contentWindow?.postMessage(JSON.stringify(message), YOUTUBE_ORIGIN)
  }, [])

  useEffect(() => {
    seekRef.current = (seconds) => {
      post({ event: "command", func: "seekTo", args: [seconds, true] })
      post({ event: "command", func: "playVideo", args: [] })
    }
    return () => {
      seekRef.current = null
    }
  }, [post, seekRef])

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== YOUTUBE_ORIGIN || event.source !== frameRef.current?.contentWindow) {
        return
      }
      const info = parsePlayerInfo(event.data)
      if (!info) return
      if (info.playing !== undefined) playingRef.current = info.playing
      const seconds = info.currentTime
      if (seconds === undefined) return
      onTimeUpdate(seconds)
      if (playingRef.current && positionSaveDue(lastSavedRef.current, seconds)) {
        savePlaybackPosition(mediaId, seconds)
        notePlaybackPosition(mediaId, seconds)
        lastSavedRef.current = seconds
      }
    }
    window.addEventListener("message", onMessage)
    return () => window.removeEventListener("message", onMessage)
  }, [mediaId, notePlaybackPosition, onTimeUpdate])

  return (
    <iframe
      ref={frameRef}
      src={src}
      title={title}
      onLoad={() => post({ event: "listening", id: videoId, channel: "widget" })}
      allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
      // YouTube refuses to play an embed whose request carries no Referer (Error 153);
      // stated explicitly so a stricter page or proxy policy can't strip it.
      referrerPolicy="strict-origin-when-cross-origin"
      allowFullScreen
      className="w-full aspect-video rounded bg-black"
    />
  )
}
