"use client"

import { DependencyList, useCallback, useEffect, useRef, useState } from "react"

type FetchEffectOptions = {
  /** While false, nothing runs and `isLoading` keeps whatever it last held. */
  enabled?: boolean
  /**
   * Also re-run on this interval. null (the default) means no polling.
   *
   * Poll runs are quiet: they never raise `isLoading`, so a background refresh
   * cannot blank a list that already has rows.
   */
  pollMs?: number | null
  /** What `isLoading` reads before the first run commits. */
  initialLoading?: boolean
}

/**
 * Runs `run` whenever `deps` change, owning the loading flag, the AbortSignal,
 * and the staleness bookkeeping that keeps a superseded response from landing.
 *
 * It deliberately does NOT own the data. `run` writes whatever state the caller
 * already has, because several call sites do real work with the response beyond
 * storing it — DownloadsCard steps back a page when the current one overshoots,
 * useSprites preloads an image, AuthContext runs a retry loop. If this hook
 * returned `data`, every one of those would need an effect watching it, which
 * is how you re-create the react-hooks/set-state-in-effect findings this whole
 * refactor exists to remove.
 *
 * ## Call-site convention: do not pass an inline `async` callback
 *
 * `react-hooks/exhaustive-deps` is registered against this hook (see
 * `additionalHooks` in eslint.config.mjs) so it validates the `deps` array,
 * which is the only thing keeping a hand-written dep list honest. The cost is
 * that it treats `run` like a useEffect callback and rejects an inline `async`
 * arrow ("Effect callbacks are synchronous to prevent race conditions").
 *
 * Both of these are fine, and cover every call site:
 *
 *   const load = useCallback(async (signal) => { ... }, [a, b])
 *   useFetchEffect(load, [load])                       // named async, by reference
 *   useFetchEffect((signal) => fetchX(a, signal).then(setRows), [a])   // non-async arrow
 */
export function useFetchEffect(
  run: (signal: AbortSignal) => Promise<unknown> | void,
  deps: DependencyList,
  options: FetchEffectOptions = {}
): { isLoading: boolean; refetch: () => void } {
  const { enabled = true, pollMs = null, initialLoading = false } = options

  const [isLoading, setIsLoading] = useState(initialLoading)
  const [refetchNonce, setRefetchNonce] = useState(0)

  // `run` closes over fresh state every render. Holding it in a ref keeps its
  // identity out of the dep list, so only `deps` decides when a fetch happens.
  // Declared before the fetch effect so it is already updated when that runs.
  const runRef = useRef(run)
  useEffect(() => {
    runRef.current = run
  })

  // Monotonic id, so a late settle from an aborted run cannot clear the flag
  // that a newer run just set.
  const runIdRef = useRef(0)

  // Marks the next run as poll-triggered. Both triggers share one nonce, so the
  // effect has no other way to tell them apart.
  const quietRef = useRef(false)

  // React's setState reads `window` even for an unmounted owner, and a test
  // environment can be torn down before an aborted run settles. Set on setup,
  // not at declaration, so StrictMode's remount turns it back on.
  const mountedRef = useRef(false)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const refetch = useCallback(() => setRefetchNonce((n) => n + 1), [])

  // A poll tick refreshes data the user is already looking at. Raising the
  // loading flag for it blanks a populated list every interval, which on a slow
  // connection is most of the time.
  const pollRefetch = useCallback(() => {
    quietRef.current = true
    setRefetchNonce((n) => n + 1)
  }, [])

  useEffect(() => {
    const quiet = quietRef.current
    quietRef.current = false

    if (!enabled) return

    const controller = new AbortController()
    const runId = ++runIdRef.current

    // Nothing to derive this from: it describes an in-flight request rather
    // than any rendered value. Every data-fetch call site routes through here
    // so that stays one setState-in-effect instead of ~20 scattered ones.
    if (!quiet) setIsLoading(true)

    let started: Promise<unknown>
    try {
      started = Promise.resolve(runRef.current(controller.signal))
    } catch (err) {
      started = Promise.reject(err)
    }

    started
      .catch((err) => {
        // Call sites handle their own errors; anything reaching here leaked,
        // and an unhandled rejection would be worse than a log line.
        if (!controller.signal.aborted) {
          console.error("useFetchEffect: unhandled error in fetch", err)
        }
      })
      .finally(() => {
        // Runs for quiet runs too: a poll that supersedes a user-initiated run
        // inherits responsibility for clearing the flag that run raised.
        if (mountedRef.current && runId === runIdRef.current) setIsLoading(false)
      })

    return () => controller.abort()
    // `deps` is the caller's, validated by exhaustive-deps at the call site.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, refetchNonce, ...deps])

  useEffect(() => {
    if (!enabled || pollMs == null) return
    const intervalId = setInterval(pollRefetch, pollMs)
    return () => clearInterval(intervalId)
  }, [enabled, pollMs, pollRefetch])

  return { isLoading, refetch }
}
