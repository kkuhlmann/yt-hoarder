"use client"

import { useState } from "react"
import {
  RectangleStackIcon,
  DocumentTextIcon,
  ForwardIcon,
  TrashIcon,
  ChevronDownIcon,
  CheckIcon,
} from "@heroicons/react/20/solid"
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover"
import { TRANSCRIPTS_SCOPE } from "@/app/lib/mediaScope"

export type MediaScope = "COMPLETE" | typeof TRANSCRIPTS_SCOPE | "SKIPPED" | "DELETED"

type ScopeMeta = {
  label: string
  icon: typeof RectangleStackIcon
  /** Trigger classes while this scope is selected. COMPLETE is the resting state, so it stays neutral. */
  triggerClass: string
}

const SCOPES: Record<MediaScope, ScopeMeta> = {
  COMPLETE: {
    label: "Library",
    icon: RectangleStackIcon,
    triggerClass: "bg-bg-surface text-text-muted hover:text-text-secondary border border-border",
  },
  TRANSCRIPTS: {
    label: "Transcripts",
    icon: DocumentTextIcon,
    triggerClass: "bg-matrix/10 text-matrix border border-matrix/30",
  },
  SKIPPED: {
    label: "Skipped",
    icon: ForwardIcon,
    triggerClass: "bg-status-warning/20 text-status-warning border border-status-warning/30",
  },
  DELETED: {
    label: "Deleted",
    icon: TrashIcon,
    triggerClass: "bg-status-error/20 text-status-error border border-status-error/30",
  },
}

const ORDER: MediaScope[] = ["COMPLETE", TRANSCRIPTS_SCOPE, "SKIPPED", "DELETED"]

function isScope(value: string): value is MediaScope {
  return value in SCOPES
}

/**
 * Which slice of the media library the list is showing.
 *
 * Skipped and Deleted read as independent filters but are one exclusive status,
 * and either of them hides the filter/view/playback controls entirely — so they
 * belong in a mode picker, not in the filter popover next door.
 */
export function ScopeSelector({
  value,
  onChange,
}: {
  value: string
  onChange: (next: MediaScope) => void
}) {
  const [open, setOpen] = useState(false)
  const scope = isScope(value) ? value : "COMPLETE"
  const { label, icon: Icon, triggerClass } = SCOPES[scope]

  const select = (next: MediaScope) => {
    onChange(next)
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          className={`inline-flex items-center gap-1.5 px-2 sm:px-2.5 py-1 rounded-md text-xs font-mono transition-colors ${triggerClass}`}
          title={scope === TRANSCRIPTS_SCOPE ? "Viewing everything with a transcript" : `Viewing ${label.toLowerCase()} media`}
        >
          <Icon className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">{label}</span>
          <ChevronDownIcon className="h-3 w-3 hidden sm:block" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-40 p-1">
        {ORDER.map((key) => {
          const OptionIcon = SCOPES[key].icon
          return (
            <button
              key={key}
              onClick={() => select(key)}
              className={`w-full flex items-center gap-2 px-2 py-1.5 rounded text-xs font-mono text-left transition-colors ${
                scope === key ? "bg-matrix/10 text-matrix" : "text-text-secondary hover:bg-bg-surface"
              }`}
            >
              <OptionIcon className="h-3.5 w-3.5 shrink-0" />
              <span className="flex-1">{SCOPES[key].label}</span>
              {scope === key && <CheckIcon className="h-3.5 w-3.5 shrink-0" />}
            </button>
          )
        })}
      </PopoverContent>
    </Popover>
  )
}
