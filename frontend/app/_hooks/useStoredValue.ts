"use client"

import { useSyncExternalStore } from "react"

/**
 * localStorage-backed preferences, read as an external store.
 *
 * The app is a static export, so localStorage isn't readable during the
 * prerender. Reading it in an effect and writing the result back to state is the
 * usual workaround; going through useSyncExternalStore gets the same
 * hydration-safe result without the extra render, and gives every component
 * reading a key the same value at the same time.
 *
 * `storage` events only fire in *other* tabs, so writes must go through
 * `writeStored` to notify subscribers in this one.
 */

const CHANGE_EVENT = "yt-hoarder:stored-value"

export function writeStored(key: string, value: string) {
  localStorage.setItem(key, value)
  window.dispatchEvent(new Event(CHANGE_EVENT))
}

function subscribe(onChange: () => void) {
  window.addEventListener(CHANGE_EVENT, onChange)
  window.addEventListener("storage", onChange)
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange)
    window.removeEventListener("storage", onChange)
  }
}

/**
 * `read` runs on every render and its result is compared with Object.is, so it
 * must return a primitive (or a memoised value) for unchanged storage.
 * `serverValue` is what the prerender and the hydration pass see.
 */
export function useStoredValue<T>(read: () => T, serverValue: T): T {
  return useSyncExternalStore(subscribe, read, () => serverValue)
}
