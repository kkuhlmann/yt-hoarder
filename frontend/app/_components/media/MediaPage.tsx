"use client"

import type { ReactNode } from "react"
import { ArrowLeftIcon } from "@heroicons/react/20/solid"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { MediaTranscript } from "@/app/_hooks/useMediaTranscript"
import { TranscriptButton } from "@/app/_components/transcript/TranscriptButton"
import { TranscriptPanel } from "@/app/_components/transcript/TranscriptPanel"

/**
 * The page an item plays on, with its transcript beside it.
 *
 * Every surface that plays something on a page (the library's video page, the queue
 * player, the no-file embed page, the audio page following the bottom bar) renders its
 * player and info as `children`; this frame adds the way back, the transcript control,
 * and the searchable panel, beside the player on wide screens and below it on phones.
 * The host owns playback, so it supplies where playback is and how to seek it.
 */
export function MediaPage({
  transcript,
  onReturn,
  returnLabel = "Return to Library",
  currentTime,
  onSeek,
  alwaysShowTranscript = false,
  children,
}: {
  transcript: MediaTranscript
  onReturn: () => void
  returnLabel?: string
  currentTime: number
  onSeek: (seconds: number) => void
  /** For a page whose whole point is the transcript: no toggle, always shown. */
  alwaysShowTranscript?: boolean
  children: ReactNode
}) {
  const { doc } = transcript
  const showPanel =
    doc !== null &&
    (alwaysShowTranscript || (transcript.open && transcript.buttonState === "show"))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button variant="outline" onClick={onReturn} className="gap-2">
          <ArrowLeftIcon className="h-4 w-4" />
          {returnLabel}
        </Button>
        {!alwaysShowTranscript && <TranscriptButton transcript={transcript} />}
      </div>
      <div className={cn("grid gap-4", showPanel && "lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]")}>
        <div className="min-w-0 space-y-4">{children}</div>
        {showPanel && (
          <TranscriptPanel
            blocks={doc.blocks}
            currentTime={currentTime}
            onSeek={onSeek}
            className="max-h-[60vh] lg:max-h-[calc(100dvh-10rem)] lg:sticky lg:top-4 lg:self-start"
          />
        )}
      </div>
    </div>
  )
}
