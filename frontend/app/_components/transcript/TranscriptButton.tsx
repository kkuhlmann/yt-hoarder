"use client"

import { ClockIcon, DocumentPlusIcon, DocumentTextIcon } from "@heroicons/react/20/solid"
import { Button } from "@/components/ui/button"
import { useTranscriptOpen, type MediaTranscript } from "@/app/_hooks/useMediaTranscript"

/**
 * The media page's transcript control: Transcript (pressed while the panel shows),
 * Generate when there is none and the user may start one, or the job's progress.
 */
export function TranscriptButton({ transcript }: { transcript: MediaTranscript }) {
  const { buttonState, generate, doc } = transcript
  const [open, setOpen] = useTranscriptOpen()

  if (buttonState === "show") {
    return (
      <Button
        variant={open ? "matrix" : "outline"}
        size="sm"
        onClick={() => setOpen(!open)}
        aria-pressed={open}
        className="gap-2"
        title={open ? "Hide the transcript" : "Show the transcript beside the player"}
      >
        <DocumentTextIcon className="h-4 w-4" />
        Transcript
      </Button>
    )
  }

  if (buttonState === "generate") {
    return (
      <Button
        variant="outline"
        size="sm"
        onClick={generate}
        className="gap-2"
        title="Transcribe this item, so it can be searched and followed along"
      >
        <DocumentPlusIcon className="h-4 w-4" />
        Generate transcript
      </Button>
    )
  }

  if (buttonState === "working") {
    const status = doc?.media.transcript_task_status
    const progress = Math.floor(doc?.media.transcript_task_progress ?? 0)
    return (
      <Button variant="outline" size="sm" disabled className="gap-2">
        <ClockIcon className="h-4 w-4 animate-pulse" />
        {status === "IN_PROGRESS" ? `Transcribing… ${progress}%` : "Transcript queued"}
      </Button>
    )
  }

  return null
}
