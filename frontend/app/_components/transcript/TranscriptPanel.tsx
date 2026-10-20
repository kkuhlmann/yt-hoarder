"use client"

import { useEffect, useRef, useState, type ReactNode } from "react"
import { MagnifyingGlassIcon, XMarkIcon } from "@heroicons/react/20/solid"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { formatTime } from "@/app/utils"
import {
  activeBlockIndex,
  filterTranscriptBlocks,
  transcriptQueryTerms,
  type TranscriptBlock,
} from "./transcript"

/** `text` with every occurrence of `terms` marked. */
function highlightTerms(text: string, terms: string[]): ReactNode {
  if (terms.length === 0) return text
  const escaped = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
  // A capturing split puts every match at an odd index, so no stateful regex test.
  return text.split(new RegExp(`(${escaped.join("|")})`, "gi")).map((part, i) =>
    i % 2 === 1 ? (
      <mark key={i} className="bg-matrix/20 text-matrix font-semibold rounded px-0.5">
        {part}
      </mark>
    ) : (
      part
    )
  )
}

/**
 * One item's transcript, searchable, following playback. Clicking a passage seeks.
 *
 * The search keeps passages containing every typed word. While it is empty the list
 * scrolls itself to the playing passage; while searching it holds still, so the results
 * don't move out from under the user.
 */
export function TranscriptPanel({
  blocks,
  currentTime,
  onSeek,
  className,
}: {
  blocks: TranscriptBlock[]
  currentTime: number
  onSeek: (seconds: number) => void
  className?: string
}) {
  const [query, setQuery] = useState("")
  const listRef = useRef<HTMLOListElement>(null)
  const terms = transcriptQueryTerms(query)
  const searching = terms.length > 0
  const shown = filterTranscriptBlocks(blocks, terms)
  const active = activeBlockIndex(blocks, currentTime)

  useEffect(() => {
    if (searching) return
    listRef.current
      ?.querySelector(`[data-index="${active}"]`)
      ?.scrollIntoView?.({ block: "nearest", behavior: "smooth" })
  }, [active, searching])

  return (
    <div className={cn("flex flex-col min-h-0 gap-2", className)}>
      <div className="relative">
        <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-text-muted" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            // Clear first; only an empty box lets Escape reach anything else.
            if (e.key === "Escape" && query) {
              e.stopPropagation()
              setQuery("")
            }
          }}
          placeholder="Search this transcript..."
          aria-label="Search this transcript"
          className="pl-9 pr-8"
        />
        {query && (
          <button
            onClick={() => setQuery("")}
            className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-text-muted hover:text-matrix"
            aria-label="Clear search"
          >
            <XMarkIcon className="h-4 w-4" />
          </button>
        )}
      </div>
      {searching && (
        <p className="text-xs font-mono text-text-muted" aria-live="polite">
          {shown.length} of {blocks.length} passages
        </p>
      )}
      <ol ref={listRef} className="min-h-0 flex-1 overflow-y-auto space-y-1 pr-1">
        {blocks.length === 0 ? (
          <li className="text-sm text-text-muted">This item has no transcript yet.</li>
        ) : shown.length === 0 ? (
          <li className="text-sm text-text-muted">No passages match &ldquo;{query.trim()}&rdquo;.</li>
        ) : (
          shown.map(({ block, index }) => (
            <li key={block.id} data-index={index}>
              <button
                onClick={() => onSeek(block.start_time)}
                className={cn(
                  "w-full text-left flex gap-3 rounded px-2 py-1.5 text-sm transition-colors",
                  index === active
                    ? "bg-matrix/10 text-text-primary"
                    : "text-text-secondary hover:bg-bg-surface"
                )}
              >
                <span className="font-mono text-xs text-matrix shrink-0 pt-0.5 w-14">
                  {formatTime(block.start_time)}
                </span>
                <span className="leading-snug">{highlightTerms(block.text, terms)}</span>
              </button>
            </li>
          ))
        )}
      </ol>
    </div>
  )
}
