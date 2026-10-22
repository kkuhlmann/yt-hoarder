import { useCallback, useEffect, useRef, useState, type RefObject } from "react"
import axios from "axios"
import toast from "react-hot-toast"
import { apiUrl, errorMessage } from "@/app/lib/api"
import { useClipRegion } from "./useClipRegion"

/** Shortest selection the backend will accept, and the floor the UI keeps. */
export const MIN_CLIP_SECONDS = 1

export type ClipOutputType = "AUDIO" | "VIDEO"

export type ClipEditor = ReturnType<typeof useClipEditor>

interface UseClipEditorProps {
  mediaDetailsId: number
  duration: number
  currentTime: number
  /**
   * The element playing the media, which the loop preview drives directly. Omit it for a
   * player with no element (the YouTube embed): the loop then follows `currentTime` and
   * moves playback through `onSeek`, which must also start it playing.
   */
  mediaRef?: RefObject<HTMLMediaElement | null>
  onSeek: (time: number) => void
  onSaved: () => void
  initialEnd?: number
  /** Sent as the clip's type. Only an item with no file takes one; others clip as what they are. */
  outputType?: ClipOutputType
}

export function useClipEditor({
  mediaDetailsId,
  duration,
  currentTime,
  mediaRef,
  onSeek,
  onSaved,
  initialEnd,
  outputType,
}: UseClipEditorProps) {
  const [startTime, setStartTime] = useState(0)
  const [rawEndTime, setEndTime] = useState(initialEnd ?? Math.min(duration || 60, 30))
  // `duration` arrives as 0 for a row whose length isn't known yet and can change
  // once the real media loads. Clamp/seed the selection end at read time rather
  // than syncing it back through an effect.
  const hasDuration = Number.isFinite(duration) && duration > 0
  const clampedEnd = !hasDuration
    ? rawEndTime
    : rawEndTime === 0
      ? Math.min(duration, 30)
      : Math.min(rawEndTime, duration)
  // End stays ahead of start even while the duration is unknown. Without this an
  // end clamped against a zero duration sits behind a start taken from mid-video,
  // and the selection reads as a negative length.
  const endWithFloor = Math.max(clampedEnd, startTime + MIN_CLIP_SECONDS)
  const endTime = hasDuration ? Math.min(endWithFloor, duration) : endWithFloor

  const [isLooping, setIsLooping] = useState(false)
  const [showSaveDialog, setShowSaveDialog] = useState(false)
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [saving, setSaving] = useState(false)
  const loopIntervalRef = useRef<NodeJS.Timeout | null>(null)

  const { handleStartTimeChange, handleEndTimeChange, handleRegionChange } = useClipRegion({
    startTime,
    setStartTime,
    endTime,
    setEndTime,
    onSeek,
  })

  // Loop preview: snap back to the region start whenever playback leaves the region.
  useEffect(() => {
    if (!isLooping || !mediaRef?.current) return

    loopIntervalRef.current = setInterval(() => {
      const el = mediaRef.current
      if (!el) return
      if (el.currentTime >= endTime || el.currentTime < startTime) {
        el.currentTime = startTime
        if (el.paused) el.play()
      }
    }, 100)

    return () => {
      if (loopIntervalRef.current) {
        clearInterval(loopIntervalRef.current)
      }
    }
  }, [isLooping, startTime, endTime, mediaRef])

  // The same loop for a player with no element, from the position it reports.
  useEffect(() => {
    if (!isLooping || mediaRef) return
    if (currentTime >= endTime || currentTime < startTime) onSeek(startTime)
  }, [isLooping, mediaRef, currentTime, startTime, endTime, onSeek])

  const handleSetStartFromCurrent = () => {
    const newStart = Math.max(0, currentTime)
    if (newStart < endTime - MIN_CLIP_SECONDS) {
      setStartTime(newStart)
    } else {
      toast.error("Start time must be at least 1 second before end time")
    }
  }

  const handleSetEndFromCurrent = () => {
    const newEnd = hasDuration ? Math.min(duration, currentTime) : currentTime
    if (newEnd > startTime + MIN_CLIP_SECONDS) {
      setEndTime(newEnd)
    } else {
      toast.error("End time must be at least 1 second after start time")
    }
  }

  const toggleLoop = useCallback(() => {
    if (!isLooping) {
      onSeek(startTime)
      mediaRef?.current?.play()
    }
    setIsLooping(!isLooping)
  }, [isLooping, startTime, onSeek, mediaRef])

  const stopLooping = useCallback(() => setIsLooping(false), [])

  const handleCreateClip = () => setShowSaveDialog(true)

  const handleSaveClip = async () => {
    if (!title.trim()) {
      toast.error("Title is required")
      return
    }
    setSaving(true)
    try {
      const response = await axios.post(apiUrl("/clips"), {
        media_details_id: mediaDetailsId,
        title: title.trim(),
        description: description.trim() || null,
        start_time: startTime,
        end_time: endTime,
        ...(outputType && { media_type: outputType }),
      })
      if (response.status === 201) {
        toast.success("Clip creation started!")
        onSaved()
      }
    } catch (error) {
      toast.error(errorMessage(error, "Failed to create clip"))
    } finally {
      setSaving(false)
    }
  }

  return {
    startTime,
    endTime,
    setStartTime,
    setEndTime,
    clipDuration: endTime - startTime,
    isLooping,
    toggleLoop,
    stopLooping,
    showSaveDialog,
    setShowSaveDialog,
    title,
    setTitle,
    description,
    setDescription,
    saving,
    handleStartTimeChange,
    handleEndTimeChange,
    handleRegionChange,
    handleSetStartFromCurrent,
    handleSetEndFromCurrent,
    handleCreateClip,
    handleSaveClip,
  }
}
