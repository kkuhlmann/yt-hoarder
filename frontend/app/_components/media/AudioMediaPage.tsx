"use client"

import { Thumb } from "@/app/_components/data/Thumb"
import { MediaInfo } from "@/app/_components/media/MediaInfo"
import { MediaPage } from "@/app/_components/media/MediaPage"
import { seekMediaElement, useMediaElementTime } from "@/app/_hooks/useMediaElementTime"
import { useMediaTranscript } from "@/app/_hooks/useMediaTranscript"
import { useMediaPlayer } from "@/app/context/MediaPlayerContext"

/**
 * The page for audio, which keeps playing in the bottom bar: it follows whatever the bar
 * plays, through a queue too, and seeks the bar's own element when a passage is picked.
 * Info and rating come from the player state, like the queue player's, so the stars here
 * and in the bar are one value.
 */
export function AudioMediaPage({
  audioElement,
  onReturn,
}: {
  audioElement: HTMLAudioElement | null
  onReturn: () => void
}) {
  const { audioPlayer, rateMedia } = useMediaPlayer()
  const playing = audioPlayer.visible && !audioPlayer.isClip
  const transcript = useMediaTranscript(playing ? audioPlayer.media_details_id : null)
  const currentTime = useMediaElementTime(audioElement)

  const seek = (seconds: number) => seekMediaElement(audioElement, seconds)

  return (
    <MediaPage transcript={transcript} onReturn={onReturn} currentTime={currentTime} onSeek={seek}>
      {!playing ? (
        <p className="text-sm text-text-muted">Nothing is playing in the audio bar.</p>
      ) : (
        <>
          <div className="relative aspect-video w-full max-w-xl mx-auto overflow-hidden rounded bg-bg-surface">
            <Thumb mediaId={audioPlayer.media_details_id} mediaType="AUDIO" alt={audioPlayer.title} />
          </div>
          <p className="text-xs font-mono text-text-muted text-center">Playing in the audio bar</p>
          <MediaInfo
            title={audioPlayer.title}
            channel={audioPlayer.channel}
            url={audioPlayer.url}
            releaseTimestamp={audioPlayer.release_timestamp}
            accessCount={audioPlayer.access_count}
            rating={audioPlayer.rating}
            onRate={(rating) => void rateMedia(rating)}
          />
        </>
      )}
    </MediaPage>
  )
}
