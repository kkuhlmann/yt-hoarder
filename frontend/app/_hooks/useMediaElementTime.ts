"use client"

import { useMemo, useSyncExternalStore } from "react"

/**
 * The playback position of a media element someone else owns (the bottom bar's audio),
 * read straight off the element so only this subscriber re-renders as it plays.
 */
export function useMediaElementTime(element: HTMLMediaElement | null): number {
  const store = useMemo(() => sampledTimeStore(element), [element])
  return useSyncExternalStore(store.subscribe, store.read, () => 0)
}

// The snapshot is the time the last event sampled, never a live read: WebKit extrapolates
// currentTime from the clock while playing, so no two reads agree, and a snapshot that
// changes on every read keeps useSyncExternalStore re-rendering until React throws.
function sampledTimeStore(element: HTMLMediaElement | null) {
  let time = element?.currentTime ?? 0
  return {
    read: () => time,
    subscribe(onChange: () => void) {
      if (!element) return () => {}
      const sample = () => {
        time = element.currentTime
        onChange()
      }
      const events = ["timeupdate", "seeked", "emptied"]
      events.forEach((event) => element.addEventListener(event, sample))
      return () => events.forEach((event) => element.removeEventListener(event, sample))
    },
  }
}

/** Moves a media element to `seconds` and plays from there; a refused play leaves it there. */
export function seekMediaElement(element: HTMLMediaElement | null, seconds: number) {
  if (!element) return
  element.currentTime = seconds
  element.play().catch(() => {})
}
