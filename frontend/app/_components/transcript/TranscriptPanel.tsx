"use client"

import { memo, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react"
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

/** Matches any of `terms`, case-insensitively; null when there are none. */
function termPattern(terms: string[]): RegExp | null {
  if (terms.length === 0) return null
  const escaped = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
  return new RegExp(`(${escaped.join("|")})`, "gi")
}

/** `text` with every match of `pattern` marked. */
function highlightTerms(text: string, pattern: RegExp | null): ReactNode {
  if (!pattern) return text
  // A capturing split puts every match at an odd index, and split never reads lastIndex,
  // so every row can share the one global pattern.
  return text.split(pattern).map((part, i) =>
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
 * One passage. Memoised, and its props hold still between playback ticks, so a tick
 * redraws only the two rows whose highlight moves; content-visibility lets the browser
 * skip laying out the rows out of view.
 */
const TranscriptRow = memo(function TranscriptRow({
  block,
  index,
  active,
  pattern,
}: {
  block: TranscriptBlock
  index: number
  active: boolean
  pattern: RegExp | null
}) {
  return (
    <li
      data-index={index}
      className="[content-visibility:auto] [contain-intrinsic-size:auto_5rem]"
    >
      <button
        className={cn(
          "w-full text-left flex gap-3 rounded px-2 py-1.5 text-sm transition-colors",
          active ? "bg-matrix/10 text-text-primary" : "text-text-secondary hover:bg-bg-surface"
        )}
      >
        <span className="font-mono text-xs text-matrix shrink-0 pt-0.5 w-14">
          {formatTime(block.start_time)}
        </span>
        <span className="leading-snug">{highlightTerms(block.text, pattern)}</span>
      </button>
    </li>
  )
})

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
  const terms = useMemo(() => transcriptQueryTerms(query), [query])
  const searching = terms.length > 0
  const shown = useMemo(() => filterTranscriptBlocks(blocks, terms), [blocks, terms])
  const pattern = useMemo(() => termPattern(terms), [terms])
  const active = activeBlockIndex(blocks, currentTime)

  useEffect(() => {
    if (searching) return
    listRef.current
      ?.querySelector(`[data-index="${active}"]`)
      ?.scrollIntoView?.({ block: "nearest", behavior: "smooth" })
  }, [active, searching])

  // One handler for the whole list rather than one per row: hosts pass a fresh onSeek
  // with every tick, which would otherwise redraw every passage several times a second.
  const seekClicked = (e: MouseEvent<HTMLOListElement>) => {
    const row = (e.target as Element).closest<HTMLElement>("[data-index]")
    if (row) onSeek(blocks[Number(row.dataset.index)].start_time)
  }

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
      <ol ref={listRef} onClick={seekClicked} className="min-h-0 flex-1 overflow-y-auto space-y-1 pr-1">
        {blocks.length === 0 ? (
          <li className="text-sm text-text-muted">This item has no transcript yet.</li>
        ) : shown.length === 0 ? (
          <li className="text-sm text-text-muted">No passages match &ldquo;{query.trim()}&rdquo;.</li>
        ) : (
          shown.map(({ block, index }) => (
            <TranscriptRow
              key={block.id}
              block={block}
              index={index}
              active={index === active}
              pattern={pattern}
            />
          ))
        )}
      </ol>
    </div>
  )
}
