/**
 * In-app links to a moment in a video: `/?media=<id>&t=<seconds>`.
 *
 * MCP search results hand these to AI agents, which put them in front of the user. A query
 * on the root page rather than a path, because the app is a single statically exported
 * page with no router — `/` is the only URL guaranteed to boot it.
 */

export type DeepLink = { mediaId: number; seconds: number }

const PARAMS = ["media", "t"] as const

export function parseDeepLink(search: string): DeepLink | null {
  const params = new URLSearchParams(search)
  const media = params.get("media")
  if (media === null || !/^\d+$/.test(media)) return null
  const mediaId = Number(media)
  if (!Number.isSafeInteger(mediaId) || mediaId <= 0) return null

  const t = Number(params.get("t") ?? 0)
  return { mediaId, seconds: Number.isFinite(t) && t > 0 ? t : 0 }
}

/** The same URL minus the deep-link params, for history.replaceState. */
export function stripDeepLink(href: string): string {
  const url = new URL(href)
  for (const param of PARAMS) url.searchParams.delete(param)
  return url.pathname + url.search + url.hash
}
