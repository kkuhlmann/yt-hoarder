"use client"

import { useCallback } from "react"
import { useStoredValue, writeStored } from "./useStoredValue"

export type ViewMode = "table" | "grid"

/**
 * Surfaces that render a list of things and can show it as a table or a grid.
 * Each remembers its own choice; see FALLBACK for the one exception.
 */
export type ViewSurface = "downloads" | "transcripts" | "playlists" | "playlistDetail"

/**
 * A surface with no stored preference of its own inherits from another one.
 *
 * Playlist detail, the Transcripts view and the media library are the same question
 * about the same objects ("how do I like to look at media?"), so picking grid in the
 * library carries over — until the user makes an explicit choice on the other surface,
 * which stores its own key and stops inheriting.
 */
const FALLBACK: Partial<Record<ViewSurface, ViewSurface>> = {
  playlistDetail: "downloads",
  transcripts: "downloads",
}

const storageKey = (surface: ViewSurface) => `viewMode:${surface}`

function isViewMode(value: string | null): value is ViewMode {
  return value === "table" || value === "grid"
}

function readStored(surface: ViewSurface): ViewMode | null {
  const own = localStorage.getItem(storageKey(surface))
  if (isViewMode(own)) return own

  const inherited = FALLBACK[surface]
  if (inherited) {
    const value = localStorage.getItem(storageKey(inherited))
    if (isViewMode(value)) return value
  }
  return null
}

/**
 * Table/grid preference for one surface, persisted in localStorage.
 *
 * Read through useStoredValue rather than in a useState initializer, so the
 * static prerender and the first client render agree — same approach as the
 * theme and visualizer preferences. Every component on the same surface now also
 * sees a change at the same time, which the old per-component state did not do.
 */
export function useViewMode(
  surface: ViewSurface,
  defaultMode: ViewMode = "grid"
): readonly [ViewMode, (next: ViewMode) => void] {
  const mode = useStoredValue(() => readStored(surface) ?? defaultMode, defaultMode)

  const setMode = useCallback(
    (next: ViewMode) => writeStored(storageKey(surface), next),
    [surface]
  )

  return [mode, setMode] as const
}
