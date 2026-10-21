"use client"

import { memo, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react"
import { MagnifyingGlassIcon, ViewfinderCircleIcon, XMarkIcon } from "@heroicons/react/20/solid"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { formatTime } from "@/app/utils"
import {
  activeBlockIndex,
  filterTranscriptBlocks,
  lockScroll,
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

function passageRow(list: HTMLElement, index: number): Element | null {
  return list.querySelector(`[data-index="${index}"]`)
}

/** How far below the top of the list's view `row` starts. */
function topIn(list: HTMLElement, row: Element): number {
  return row.getBoundingClientRect().top - list.getBoundingClientRect().top
}

/**
 * Scrolls the list alone. scrollIntoView would move the page too, and on a phone, where
 * the panel sits below the player, that pulls the video out of view.
 */
function scrollListBy(list: HTMLElement, by: number, smooth: boolean) {
  list.scrollTo({ top: list.scrollTop + by, behavior: smooth ? "smooth" : "instant" })
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
 * The search keeps passages containing every typed word. While it is empty and Follow is
 * on, the list keeps the playing passage second from the top, with the one before it above;
 * while searching it holds still, so the results don't move out from under the user. Any
 * scroll of the list by the user turns Follow off, and Follow, a click on a passage, or a
 * change to the search turns it back on.
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
  // The transcript the user turned Follow off for, so the next item in a queue starts out
  // following.
  const [unfollowed, setUnfollowed] = useState<TranscriptBlock[] | null>(null)
  const listRef = useRef<HTMLOListElement>(null)
  // Set by the user's own scrolling and cleared by ours, so only theirs turns Follow off.
  const userScroll = useRef(false)
  // The passage playing when one was clicked. The host's time catches up with the seek a
  // tick later, and following the stale passage until then would jump away from the click.
  const seekedFrom = useRef<number | null>(null)
  const terms = useMemo(() => transcriptQueryTerms(query), [query])
  const searching = terms.length > 0
  const shown = useMemo(() => filterTranscriptBlocks(blocks, terms), [blocks, terms])
  const pattern = useMemo(() => termPattern(terms), [terms])
  const active = activeBlockIndex(blocks, currentTime)
  const following = !searching && unfollowed !== blocks

  useEffect(() => {
    const list = listRef.current
    if (searching || !following || !list || seekedFrom.current === active) return
    seekedFrom.current = null
    const row = passageRow(list, active)
    const anchor = passageRow(list, Math.max(active - 1, 0))
    if (!row || !anchor) return
    const scroll = lockScroll(topIn(list, anchor), list.clientHeight)
    if (!scroll) return
    userScroll.current = false
    scrollListBy(list, scroll.by, scroll.smooth)
    if (scroll.smooth) return
    // The rows a jump lands among grow from their placeholder height once laid out, which
    // moves the passage off its place again. They grow in that same frame, and an observer
    // hears it after layout but before paint, so the re-aligning is never seen; a
    // requestAnimationFrame pass would paint one frame of the wrong passages first.
    const settle = new ResizeObserver(() => {
      if (userScroll.current) return
      const by = topIn(list, anchor)
      if (Math.abs(by) >= 1) scrollListBy(list, by, false)
    })
    settle.observe(anchor)
    settle.observe(row)
    return () => settle.disconnect()
  }, [active, blocks, searching, following])

  const resumeFollowing = () => {
    userScroll.current = false
    seekedFrom.current = null
    setUnfollowed(null)
  }

  const changeQuery = (next: string) => {
    setQuery(next)
    resumeFollowing()
  }

  const markUserScroll = () => {
    userScroll.current = true
  }

  // A tap that never scrolled. Left set, the flag would let a later scroll event from layout
  // (rows growing out of their placeholders) pass for the user's and turn Follow off.
  const endTouch = () => {
    if (following) userScroll.current = false
  }

  const scrolled = () => {
    if (userScroll.current && following) setUnfollowed(blocks)
  }

  // One handler for the whole list rather than one per row: hosts pass a fresh onSeek
  // with every tick, which would otherwise redraw every passage several times a second.
  const seekClicked = (e: MouseEvent<HTMLOListElement>) => {
    const row = (e.target as Element).closest<HTMLElement>("[data-index]")
    if (!row) return
    userScroll.current = false
    seekedFrom.current = active
    setUnfollowed(null)
    onSeek(blocks[Number(row.dataset.index)].start_time)
  }

  return (
    <div className={cn("flex flex-col min-h-0 gap-2", className)}>
      <div className="flex gap-2">
        <div className="relative flex-1">
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
        <Button
          variant={following ? "matrix" : "outline"}
          onClick={() => (following ? setUnfollowed(blocks) : changeQuery(""))}
          aria-pressed={following}
          className="h-10 gap-2"
          title={
            following
              ? "Keeping the playing passage in view. Click to stop following"
              : "Jump back to the playing passage and keep it in view"
          }
        >
          <ViewfinderCircleIcon className="h-4 w-4" />
          Follow
        </Button>
      </div>
      {searching && (
        <p className="text-xs font-mono text-text-muted" aria-live="polite">
          {shown.length} of {blocks.length} passages
        </p>
      )}
      <ol
        ref={listRef}
        onClick={seekClicked}
        onScroll={scrolled}
        onWheel={markUserScroll}
        onTouchStart={markUserScroll}
        onPointerDown={markUserScroll}
        onKeyDown={markUserScroll}
        onTouchEnd={endTouch}
        onPointerUp={endTouch}
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
    </div>
  )
}
