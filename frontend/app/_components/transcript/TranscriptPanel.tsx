"use client"

import { memo, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react"
import {
  ArrowDownIcon,
  ArrowUpIcon,
  MagnifyingGlassIcon,
  XMarkIcon,
} from "@heroicons/react/20/solid"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { formatTime } from "@/app/utils"
import {
  activeBlockIndex,
  centerOffset,
  filterTranscriptBlocks,
  followScroll,
  transcriptQueryTerms,
  type RowSpan,
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

function passageRow(list: HTMLElement, index: number): Element | null {
  return list.querySelector(`[data-index="${index}"]`)
}

function spanIn(list: HTMLElement, row: Element): RowSpan {
  const top = list.getBoundingClientRect().top
  const box = row.getBoundingClientRect()
  return { top: box.top - top, bottom: box.bottom - top }
}

/**
 * Scrolls the list alone. scrollIntoView would move the page too, and on a phone, where
 * the panel sits below the player, that pulls the video out of view.
 */
function scrollListBy(list: HTMLElement, by: number, smooth: boolean) {
  list.scrollTo({ top: list.scrollTop + by, behavior: smooth ? "smooth" : "instant" })
}

/**
 * The first and last passages wholly in view in an unfiltered list, where row i is
 * passage i. The rows run in order, so it bisects rather than measuring every one.
 */
function shownRange(list: HTMLElement): { first: number; last: number } {
  const view = list.getBoundingClientRect()
  const rows = list.children
  const firstWhere = (test: (box: DOMRect) => boolean) => {
    let lo = 0
    let hi = rows.length
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (test(rows[mid].getBoundingClientRect())) hi = mid
      else lo = mid + 1
    }
    return lo
  }
  return {
    first: firstWhere((box) => box.top >= view.top - 1),
    last: firstWhere((box) => box.bottom > view.bottom + 1) - 1,
  }
}

type Pause = { blocks: TranscriptBlock[]; first: number; last: number }

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
 * don't move out from under the user. Scrolling the playing passage out of view also
 * stops the following, and Back to playback, a click on a passage, or a new search
 * resumes it.
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
  // The passages the user scrolled to, while the playing one is out of their view. Tagged
  // with its transcript, so the next item in a queue starts out following.
  const [pause, setPause] = useState<Pause | null>(null)
  const listRef = useRef<HTMLOListElement>(null)
  // Set by the user's own scrolling and cleared by ours, so only theirs pauses following.
  const userScroll = useRef(false)
  // The passage playing when one was clicked. The host's time catches up with the seek a
  // tick later, and following the stale passage until then would jump away from the click.
  const seekedFrom = useRef<number | null>(null)
  const terms = useMemo(() => transcriptQueryTerms(query), [query])
  const searching = terms.length > 0
  const shown = useMemo(() => filterTranscriptBlocks(blocks, terms), [blocks, terms])
  const pattern = useMemo(() => termPattern(terms), [terms])
  const active = activeBlockIndex(blocks, currentTime)
  const paused = !searching && pause?.blocks === blocks ? pause : null
  const following = paused === null
  const away = paused && (active < paused.first ? "up" : active > paused.last ? "down" : null)

  useEffect(() => {
    const list = listRef.current
    if (searching || !following || !list || seekedFrom.current === active) return
    seekedFrom.current = null
    const row = passageRow(list, active)
    if (!row) return
    const scroll = followScroll(spanIn(list, row), list.clientHeight)
    if (!scroll) return
    userScroll.current = false
    scrollListBy(list, scroll.by, scroll.smooth)
    if (scroll.smooth) return
    // The rows a jump lands among grow from their placeholder height once laid out, which
    // pushes the passage out of view again. The passage grows in that same frame, and an
    // observer hears it after layout but before paint, so the re-centering is never seen;
    // a requestAnimationFrame pass would paint one frame of the wrong passages first.
    const settle = new ResizeObserver(() => {
      if (userScroll.current) return
      scrollListBy(list, centerOffset(spanIn(list, row), list.clientHeight), false)
    })
    settle.observe(row)
    return () => settle.disconnect()
  }, [active, blocks, searching, following])

  const resumeFollowing = () => {
    seekedFrom.current = null
    setPause(null)
  }

  const changeQuery = (next: string) => {
    setQuery(next)
    resumeFollowing()
  }

  const markUserScroll = () => {
    userScroll.current = true
  }

  const scrolled = () => {
    const list = listRef.current
    if (!userScroll.current || searching || !list || blocks.length === 0) return
    const { first, last } = shownRange(list)
    const inView = active >= first && active <= last
    setPause((current) => {
      if (inView) return null
      const unchanged =
        current?.blocks === blocks && current.first === first && current.last === last
      return unchanged ? current : { blocks, first, last }
    })
  }

  // One handler for the whole list rather than one per row: hosts pass a fresh onSeek
  // with every tick, which would otherwise redraw every passage several times a second.
  const seekClicked = (e: MouseEvent<HTMLOListElement>) => {
    const row = (e.target as Element).closest<HTMLElement>("[data-index]")
    if (!row) return
    seekedFrom.current = active
    setPause(null)
    onSeek(blocks[Number(row.dataset.index)].start_time)
  }

  return (
    <div className={cn("flex flex-col min-h-0 gap-2", className)}>
      <div className="relative">
        <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-text-muted" />
        <Input
          value={query}
          onChange={(e) => changeQuery(e.target.value)}
          onKeyDown={(e) => {
            // Clear first; only an empty box lets Escape reach anything else.
            if (e.key === "Escape" && query) {
              e.stopPropagation()
              changeQuery("")
            }
          }}
          placeholder="Search this transcript..."
          aria-label="Search this transcript"
          className="pl-9 pr-8"
        />
        {query && (
          <button
            onClick={() => changeQuery("")}
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
      <div className="relative flex min-h-0 flex-1 flex-col">
        <ol
          ref={listRef}
          onClick={seekClicked}
          onScroll={scrolled}
          onWheel={markUserScroll}
          onTouchStart={markUserScroll}
          onPointerDown={markUserScroll}
          onKeyDown={markUserScroll}
          className="min-h-0 flex-1 overflow-y-auto space-y-1 pr-1"
        >
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
        {away && (
          <Button
            size="sm"
            onClick={resumeFollowing}
            className="absolute bottom-2 left-1/2 -translate-x-1/2 gap-2 rounded-full shadow-glow"
          >
            {away === "up" ? (
              <ArrowUpIcon className="h-4 w-4" />
            ) : (
              <ArrowDownIcon className="h-4 w-4" />
            )}
            Back to playback
          </Button>
        )}
      </div>
    </div>
  )
}
