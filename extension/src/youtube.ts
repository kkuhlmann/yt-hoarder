/**
 * Which YT-Hoarder actions a page URL supports, by the same rules the server applies
 * (`backend/app/ytdlp/urls.py`). Keep the two in step: the popup offers Subscribe only for a URL
 * the server's subscription job accepts, so a drift here becomes a subscription that silently fails.
 */

export type PageTargets = {
  /** Canonical watch URL, when the page is a single video. */
  video?: string
  /** Canonical playlist URL, when the URL carries `list=`. */
  playlist?: string
  /** Channel URL, when the path is a channel or one of its tabs. */
  channel?: string
}

const YOUTUBE_HOSTS = new Set(["youtube.com", "youtu.be", "music.youtube.com"])
const CHANNEL_PATTERNS = ["/@", "/channel/", "/c/", "/user/"]
const VIDEO_PATH_PREFIXES = new Set(["shorts", "embed", "live"])

// The server strips every occurrence of each prefix, not just a leading one; matching that keeps
// the two classifications identical on odd hosts.
function bareHost(url: URL): string {
  return url.hostname.toLowerCase().replaceAll("www.", "").replaceAll("m.", "")
}

// Python's parse_qs drops blank values, so `?v=` counts as absent there.
function firstValue(params: URLSearchParams, name: string): string | undefined {
  return params.getAll(name).find((value) => value !== "")
}

function trimSlashes(path: string): string {
  return path.replace(/^\/+|\/+$/g, "")
}

function videoId(url: URL, host: string): string | undefined {
  if (host === "youtu.be") return trimSlashes(url.pathname) || undefined
  if (host !== "youtube.com" && host !== "music.youtube.com") return undefined
  const fromQuery = firstValue(url.searchParams, "v")
  if (fromQuery) return fromQuery
  const parts = trimSlashes(url.pathname).split("/")
  if (parts.length >= 2 && VIDEO_PATH_PREFIXES.has(parts[0])) return parts[1] || undefined
  return undefined
}

function isChannel(url: URL, host: string): boolean {
  if (host !== "youtube.com") return false
  const path = url.pathname.toLowerCase()
  return CHANNEL_PATTERNS.some((pattern) => path.includes(pattern))
}

export function classifyPage(rawUrl: string): PageTargets {
  let url: URL
  try {
    url = new URL(rawUrl)
  } catch {
    return {}
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return {}

  const host = bareHost(url)
  if (!YOUTUBE_HOSTS.has(host)) return {}

  const targets: PageTargets = {}
  const id = videoId(url, host)
  if (id) targets.video = `https://www.youtube.com/watch?v=${id}`
  const list = firstValue(url.searchParams, "list")
  if (list) targets.playlist = `https://www.youtube.com/playlist?list=${list}`
  if (isChannel(url, host)) {
    targets.channel = `https://www.youtube.com${url.pathname.replace(/\/+$/, "")}`
  }
  return targets
}
