import { DEV_API_PORT } from "./api"

// Keep in step with `DEV_WEB_PORT` in extension/src/api.ts, which mirrors `DEV_API_PORT` too.
const DEV_WEB_PORT = "3000"

/**
 * The server URL to paste into the browser extension's settings.
 *
 * `apiBase` is `apiUrl("")`: relative `/api` in production, already absolute
 * (`http://host:8000`) in dev. The extension takes the API base or the app's own URL, so
 * this resolves the API base the way `mcpServerUrl` does, without a trailing slash.
 *
 * In dev that base is the default `:8000` of the host the page was opened on. The extension
 * finds it from the web app's `:3000` address (`apiBaseCandidates`), so that address, the one
 * people actually browse to, is offered instead.
 */
export function extensionServerUrl(apiBase: string, origin: string): string {
  const page = new URL(origin)
  const api = new URL(apiBase || "/", page)
  const devApi = `${page.protocol}//${page.hostname}:${DEV_API_PORT}`
  if (page.port === DEV_WEB_PORT && api.origin === devApi) return page.origin
  return api.href.replace(/\/$/, "")
}
