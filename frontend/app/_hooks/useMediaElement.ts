"use client"

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react"
import { apiUrl } from "@/app/lib/api"
import { mediaApi } from "@/app/lib/mediaApi"
import { savePlaybackPosition } from "@/app/lib/playbackSync"
import {
  reportMediaSessionPosition,
  useMediaSessionActionHandlers,
  type MediaSessionQueue,
} from "@/app/_hooks/useMediaSession"
import { useMediaPlayer } from "@/app/context/MediaPlayerContext"

/** The track an element starts by itself when the current one ends. */
export type AutoAdvance = {
  id: number
  startTime: number
  duration?: number
}

export type UseMediaElementOptions = {
  id: number
  startTime?: number
  initialDuration?: number
  /**
   * Treat `startTime` as a timestamp the user picked rather than a resume
   * position, so the near-the-end reset below doesn't apply.
   */
  exactStart?: boolean
  isClip?: boolean
  onEnded?: () => void
  onTimeUpdate?: (time: number) => void
  queue?: MediaSessionQueue
  /**
   * Started on this same element inside the `ended` event, ahead of the render
   * that `onEnded` triggers. That render must then arrive with this `id` and
   * `startTime`, or the element reloads to whatever it says.
   */
  autoAdvance?: AutoAdvance
}

/** A resume position within 30s of the end restarts from 0. */
export function effectiveStartTime(
  startTime: number,
  duration: number | undefined,
  exactStart: boolean
): number {
  return !exactStart && duration && startTime > duration - 30 ? 0 : startTime
}

/**
 * Playback lifecycle shared by every media surface: transport state, element
 * event wiring, Media Session reporting, position persistence and the transport
 * callbacks. Works for <video> and <audio> alike; anything element-specific
 * (fullscreen, PiP, the audio visualizer) stays with the caller.
 *
 * The element's `src` is owned here, not by JSX: see the layout effect below.
 */
export function useMediaElement<T extends HTMLMediaElement>(
  mediaRef: RefObject<T | null>,
  {
    id,
    startTime = 0,
    initialDuration,
    exactStart = false,
    isClip = false,
    onEnded,
    onTimeUpdate,
    queue,
    autoAdvance,
  }: UseMediaElementOptions
) {
  const { notePlaybackPosition } = useMediaPlayer()

  // Throttle marker for the position PATCH below. Nothing renders it, so a ref
  // keeps the save out of the render cycle entirely.
  const lastSavedPositionRef = useRef(0)
  // The track handleEnded started on its own, until a render names another.
  const adoptedRef = useRef<{ id: number; startTime: number } | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(initialDuration || 0)
  const [volume, setVolume] = useState(1)
  const [isMuted, setIsMuted] = useState(false)
  const [playbackRate, setPlaybackRate] = useState(1)

  const mediaSrc = isClip ? apiUrl(mediaApi.clip(id)) : apiUrl(mediaApi.stream(id))

  // Assigned here rather than as a JSX prop, and only when it differs: setting
  // `src`, even to its current value, re-runs the load algorithm, which pauses
  // the element. As a prop, the render that follows an auto-advance would
  // re-set it and stop the track handleEnded just started.
  useLayoutEffect(() => {
    const el = mediaRef.current
    if (!el) return
    if (adoptedRef.current && adoptedRef.current.id !== id) adoptedRef.current = null
    if (el.getAttribute("src") !== mediaSrc) el.src = mediaSrc
  }, [mediaSrc, id, mediaRef])

  useEffect(() => {
    const el = mediaRef.current
    if (!el || typeof startTime !== "number") return
    // Already started at this position; seeking now would restart a track
    // that may have been playing for minutes while the render was deferred.
    const adopted = adoptedRef.current
    if (adopted && adopted.id === id && adopted.startTime === startTime) {
      setCurrentTime(el.currentTime)
      return
    }
    const start = effectiveStartTime(startTime, initialDuration, exactStart)
    el.currentTime = start
    setCurrentTime(start)
  }, [startTime, id, initialDuration, exactStart, mediaRef])

  // Explicit play() for iOS playback reliability — the autoPlay attribute is
  // unreliable on iOS Safari (and doesn't work in background at all).
  useEffect(() => {
    const el = mediaRef.current
    if (!el) return

    const attemptPlay = () => {
      el.play().catch(() => {
        // iOS may block autoplay; user can tap the play button
      })
    }

    if (el.readyState >= 2) {
      attemptPlay()
    } else {
      el.addEventListener("canplay", attemptPlay, { once: true })
      return () => el.removeEventListener("canplay", attemptPlay)
    }
  }, [id, mediaRef])

  const advanceId = autoAdvance?.id
  const advanceStart = autoAdvance?.startTime
  const advanceDuration = autoAdvance?.duration

  useEffect(() => {
    const el = mediaRef.current
    if (!el) return

    const handleTimeUpdateEvent = () => {
      reportMediaSessionPosition(el)
      // The element is already on an auto-advanced track this render's `id`
      // doesn't describe; reporting now would save its position as `id`'s.
      if (adoptedRef.current && adoptedRef.current.id !== id) return
      setCurrentTime(el.currentTime)
      onTimeUpdate?.(el.currentTime)
    }
    const handleLoadedMetadata = () => setDuration(el.duration)
    const handlePlay = () => {
      setIsPlaying(true)
      if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "playing"
    }
    const handlePause = () => {
      setIsPlaying(false)
      if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "paused"
    }
    const handleEnded = () => {
      if (isClip) return
      setIsPlaying(false)
      if (advanceId !== undefined && advanceStart !== undefined) {
        // iOS suspends a locked phone's page soon after its audio stops, so
        // the next track must be playing before this handler returns. Left to
        // the re-render onEnded causes, it would start only on unlock. play()
        // is allowed here because a gesture already started this element.
        adoptedRef.current = { id: advanceId, startTime: advanceStart }
        el.src = apiUrl(mediaApi.stream(advanceId))
        el.currentTime = effectiveStartTime(advanceStart, advanceDuration, false)
        el.play().catch(() => {})
      }
      onEnded?.()
    }

    el.addEventListener("timeupdate", handleTimeUpdateEvent)
    el.addEventListener("loadedmetadata", handleLoadedMetadata)
    el.addEventListener("play", handlePlay)
    el.addEventListener("pause", handlePause)
    el.addEventListener("ended", handleEnded)
    return () => {
      el.removeEventListener("timeupdate", handleTimeUpdateEvent)
      el.removeEventListener("loadedmetadata", handleLoadedMetadata)
      el.removeEventListener("play", handlePlay)
      el.removeEventListener("pause", handlePause)
      el.removeEventListener("ended", handleEnded)
    }
  }, [id, onTimeUpdate, isClip, onEnded, mediaRef, advanceId, advanceStart, advanceDuration])

  useMediaSessionActionHandlers(mediaRef, id, queue)

  useEffect(() => {
    if (isClip) return
    if (Math.abs(currentTime - lastSavedPositionRef.current) >= 5) {
      savePlaybackPosition(id, currentTime)
      lastSavedPositionRef.current = currentTime
      // Publish on the same beat as the save, so a list's progress bar and the
      // stored position can never disagree.
      notePlaybackPosition(id, currentTime)
    }
  }, [currentTime, id, isClip, notePlaybackPosition])

  const togglePlay = useCallback(() => {
    const el = mediaRef.current
    if (!el) return
    if (isPlaying) el.pause()
    else el.play()
  }, [isPlaying, mediaRef])

  const handleSeek = useCallback(
    (value: number) => {
      const el = mediaRef.current
      if (!el) return
      el.currentTime = value
      setCurrentTime(value)
    },
    [mediaRef]
  )

  const handleVolumeChange = useCallback(
    (value: number) => {
      const el = mediaRef.current
      if (!el) return
      el.volume = value
      setVolume(value)
      setIsMuted(value === 0)
    },
    [mediaRef]
  )

  const toggleMute = useCallback(() => {
    const el = mediaRef.current
    if (!el) return
    if (isMuted) {
      el.volume = volume || 0.5
      setIsMuted(false)
    } else {
      el.volume = 0
      setIsMuted(true)
    }
  }, [isMuted, volume, mediaRef])

  const skip = useCallback(
    (seconds: number) => {
      const el = mediaRef.current
      if (!el) return
      const newTime = Math.max(0, Math.min(el.currentTime + seconds, duration))
      el.currentTime = newTime
      setCurrentTime(newTime)
    },
    [duration, mediaRef]
  )

  const handlePlaybackRateChange = useCallback(
    (rate: string) => {
      const numRate = parseFloat(rate)
      if (mediaRef.current) mediaRef.current.playbackRate = numRate
      setPlaybackRate(numRate)
    },
    [mediaRef]
  )

  return {
    isPlaying,
    currentTime,
    duration,
    volume,
    isMuted,
    playbackRate,
    togglePlay,
    handleSeek,
    handleVolumeChange,
    toggleMute,
    skip,
    handlePlaybackRateChange,
    setCurrentTime,
    setDuration,
  }
}
