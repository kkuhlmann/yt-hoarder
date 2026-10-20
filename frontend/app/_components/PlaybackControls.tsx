"use client"

import { PlayIcon, ArrowsRightLeftIcon, PlayPauseIcon } from "@heroicons/react/20/solid"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export type QueueMode = "off" | "ordered" | "shuffled"

type ResumeToggleProps = {
  checked: boolean
  disabled?: boolean
  onChange: (checked: boolean) => void
  /** What resuming means on this surface; defaults to the player's wording. */
  title?: string
}

/**
 * Resume, as a button that stays pressed: highlighted while on, styled like its neighbours
 * while off, the same way Play All and Shuffle show a running queue.
 */
export function ResumeToggle({ checked, disabled = false, onChange, title }: ResumeToggleProps) {
  return (
    <Button
      variant={checked ? "matrix" : "outline"}
      size="sm"
      onClick={() => onChange(!checked)}
      disabled={disabled}
      aria-pressed={checked}
      className="gap-2"
      title={
        disabled
          ? "Play All and Shuffle always start each track from the beginning"
          : (title ??
            "Resume each track from where you left off, and show how far through each one you are")
      }
    >
      <PlayPauseIcon className="h-4 w-4" />
      <span className="hidden sm:inline">Resume</span>
    </Button>
  )
}

type PlaybackControlsProps = {
  onPlayAll: () => void
  onShuffle: () => void
  resume: ResumeToggleProps
  /**
   * Only the library owns a queue it can detach, so only it has an on/off state to
   * reflect. Where it's absent the two buttons are plain actions, which is what the
   * playlist and tag-mix surfaces need.
   */
  queueMode?: QueueMode
  disabled?: boolean
  playAllTitle?: string
  shuffleTitle?: string
  className?: string
}

export function PlaybackControls({
  onPlayAll,
  onShuffle,
  resume,
  queueMode,
  disabled = false,
  playAllTitle,
  shuffleTitle,
  className,
}: PlaybackControlsProps) {
  const ordered = queueMode === "ordered"
  const shuffled = queueMode === "shuffled"

  return (
    <div className={cn("flex items-center gap-1.5 sm:gap-2", className)}>
      <Button
        variant={queueMode === undefined || ordered ? "matrix" : "outline"}
        size="sm"
        onClick={onPlayAll}
        disabled={disabled}
        {...(queueMode !== undefined && { "aria-pressed": ordered })}
        className="gap-2"
        title={
          playAllTitle ??
          (ordered
            ? "Stop after this track"
            : shuffled
              ? "Play the current queue in order"
              : "Play everything matching the current filter")
        }
      >
        <PlayIcon className="h-4 w-4" />
        <span className="hidden sm:inline">Play All</span>
      </Button>
      <Button
        variant={shuffled ? "matrix" : "outline"}
        size="sm"
        onClick={onShuffle}
        disabled={disabled}
        {...(queueMode !== undefined && { "aria-pressed": shuffled })}
        className="gap-2"
        title={
          shuffleTitle ??
          (shuffled
            ? "Stop after this track"
            : ordered
              ? "Shuffle the current queue"
              : "Shuffle everything matching the current filter")
        }
      >
        <ArrowsRightLeftIcon className="h-4 w-4" />
        <span className="hidden sm:inline">Shuffle</span>
      </Button>
      <ResumeToggle {...resume} />
    </div>
  )
}
