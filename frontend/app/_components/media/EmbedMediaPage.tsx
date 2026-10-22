"use client"

import { useEffect, useRef, useState } from "react"
import toast from "react-hot-toast"
import { formatTime } from "@/app/utils"
import { LoadingSpinner } from "@/app/_components/LoadingSpinner"
import { EmbedClipControls } from "@/app/_components/media/EmbedClipControls"
import { MediaInfo } from "@/app/_components/media/MediaInfo"
import { MediaPage } from "@/app/_components/media/MediaPage"
import { YouTubeEmbed } from "@/app/_components/transcript/YouTubeEmbed"
import {
  resumePosition,
  sourceUrlAt,
  type TranscriptTarget,
} from "@/app/_components/transcript/transcript"
import { MIN_CLIP_SECONDS, useClipEditor, type ClipOutputType } from "@/app/_hooks/useClipEditor"
import { saveRating } from "@/app/_hooks/useMediaActions"
import { useMediaTranscript } from "@/app/_hooks/useMediaTranscript"
import { useMediaPlayer } from "@/app/context/MediaPlayerContext"
import { useOffline } from "@/app/context/OfflineContext"

/**
 * The page for an item with no file to play: a transcript-only item, or deleted media
 * that kept its transcript. It plays through the YouTube embed (or, for another site,
 * links out), and the transcript is always shown, since it is the point of the page.
 */
export function EmbedMediaPage({
  target,
  onReturn,
}: {
  target: TranscriptTarget
  onReturn: () => void
}) {
  // Keyed so each opening starts from its own moment, not the last one's.
  return <EmbedPageBody key={`${target.mediaId}:${target.seconds}`} target={target} onReturn={onReturn} />
}

function EmbedPageBody({ target, onReturn }: { target: TranscriptTarget; onReturn: () => void }) {
  const transcript = useMediaTranscript(target.mediaId)
  const { doc, failed, patchMedia } = transcript
  const { offlineMode } = useOffline()
  // null until the embed reports, so a resume shows its own position before playback.
  const [reportedTime, setReportedTime] = useState<number | null>(target.seconds)
  const [startedOver, setStartedOver] = useState(false)
  const [clipping, setClipping] = useState(false)
  const [outputType, setOutputType] = useState<ClipOutputType>("VIDEO")
  const seekRef = useRef<((seconds: number) => void) | null>(null)

  // One thing plays at a time: the embed starts playing on its own.
  const { closeAudioPlayer } = useMediaPlayer()
  useEffect(() => {
    closeAudioPlayer()
  }, [closeAudioPlayer])

  const startAt = doc ? (target.seconds ?? resumePosition(doc.media)) : 0
  const currentTime = reportedTime ?? startAt
  const offerStartOver =
    target.seconds === null && startAt > 0 && !startedOver && doc?.youtube_id != null

  const seek = (seconds: number) => {
    setReportedTime(seconds)
    if (doc && !doc.youtube_id) {
      window.open(sourceUrlAt(doc.media.url, seconds), "_blank", "noopener,noreferrer")
      return
    }
    seekRef.current?.(seconds)
  }

  // The row's duration can be missing; the transcript's last passage is a floor for it.
  const duration = doc
    ? doc.media.duration || (doc.blocks.at(-1)?.end_time ?? 0)
    : 0

  const exitClipping = () => {
    setClipping(false)
    clipEditor.stopLooping()
    clipEditor.setShowSaveDialog(false)
    clipEditor.setTitle("")
    clipEditor.setDescription("")
  }

  const clipEditor = useClipEditor({
    mediaDetailsId: target.mediaId,
    duration,
    currentTime,
    onSeek: seek,
    onSaved: exitClipping,
    outputType,
  })

  const enterClipping = () => {
    const start = Math.max(0, currentTime - 5)
    const end = duration > 0 ? Math.min(duration, currentTime + 10) : currentTime + 10
    clipEditor.setStartTime(start)
    clipEditor.setEndTime(Math.max(end, start + MIN_CLIP_SECONDS))
    setClipping(true)
  }

  const canClip = doc?.youtube_id != null && !offlineMode

  const rate = async (rating: number | null) => {
    if (!doc) return
    const previous = doc.media.rating
    patchMedia({ rating })
    try {
      await saveRating(doc.media.id, rating)
    } catch {
      toast.error("Failed to update rating")
      patchMedia({ rating: previous })
    }
  }

  return (
    <MediaPage
      transcript={transcript}
      onReturn={onReturn}
      currentTime={currentTime}
      onSeek={seek}
      alwaysShowTranscript
    >
      {failed ? (
        <p className="text-sm text-status-error">This item couldn&apos;t be loaded.</p>
      ) : !doc ? (
        <LoadingSpinner />
      ) : (
        <>
          {doc.youtube_id ? (
            <YouTubeEmbed
              mediaId={doc.media.id}
              videoId={doc.youtube_id}
              start={startAt}
              title={doc.media.title ?? "Video"}
              onTimeUpdate={setReportedTime}
              seekRef={seekRef}
            />
          ) : (
            <p className="text-sm text-text-muted">
              This source can&apos;t be played here. Pick a passage to open it there.
            </p>
          )}
          {offerStartOver && (
            <p className="text-xs font-mono text-text-muted text-center">
              Resumed at {formatTime(startAt)} ·{" "}
              <button
                onClick={() => {
                  setStartedOver(true)
                  seek(0)
                }}
                className="text-matrix hover:underline"
              >
                Start over
              </button>
            </p>
          )}
          {canClip && (
            <EmbedClipControls
              editor={clipEditor}
              clipping={clipping}
              onEnter={enterClipping}
              onExit={exitClipping}
              duration={duration}
              currentTime={currentTime}
              outputType={outputType}
              onOutputTypeChange={setOutputType}
            />
          )}
          <MediaInfo
            title={doc.media.title ?? "Untitled"}
            channel={doc.media.channel}
            url={sourceUrlAt(doc.media.url, currentTime)}
            urlLabel={`Open source at ${formatTime(currentTime)}`}
            releaseTimestamp={doc.media.release_timestamp}
            accessCount={doc.media.access_count > 0 ? doc.media.access_count : undefined}
            // As in the library, deleted media shows no rating.
            rating={doc.media.status === "COMPLETE" ? doc.media.rating : undefined}
            onRate={rate}
          />
        </>
      )}
    </MediaPage>
  )
}
