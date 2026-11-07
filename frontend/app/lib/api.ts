/**
 * API URL utility for handling dev vs production environments.
 *
 * Production bakes NEXT_PUBLIC_BACKEND_API=/api (Dockerfile.prod) and FastAPI serves
 * the API and the static export from one origin. Dev leaves it unset and follows the
 * browser's own address, so a single dev server answers on localhost, a LAN IP or a
 * Tailscale name without a rebuild. Set the variable to override that -- a reverse
 * proxy or TLS termination, where the API is not on :8000 of the host you browse to.
 */

import axios from "axios"

import { readOfflineMode } from "./offlineMode"

export const DEV_API_PORT = "8000"

function apiBase(): string {
  const configured = process.env.NEXT_PUBLIC_BACKEND_API
  if (configured) return configured
  // No window during the static-export prerender or the node-env unit tests, and
  // same-origin is the right answer for a production build either way.
  if (process.env.NODE_ENV === "production" || typeof window === "undefined") return ""
  return `${window.location.protocol}//${window.location.hostname}:${DEV_API_PORT}`
}

// Send cookies with all requests (auth_token cookie)
axios.defaults.withCredentials = true

// Global 401 interceptor: dispatch a custom event so AuthContext can handle it
axios.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Don't redirect for auth endpoints themselves
      const url = error.config?.url || ''
      // In offline mode the cookie may well have expired while the device was away
      // from the server. Signing the user out over that would take away the
      // downloaded library too, which is the one thing still usable.
      if (!url.includes('/auth/') && !readOfflineMode()) {
        window.dispatchEvent(new Event('auth:unauthorized'))
      }
    }
    return Promise.reject(error)
  }
)

/**
 * Constructs a full API URL from a path.
 * @param path - The API path (should start with /)
 * @returns The full URL (e.g., "http://192.168.1.50:8000/subscriptions" or "/api/subscriptions")
 */
export function apiUrl(path: string): string {
  return `${apiBase()}${path}`
}

/**
 * Extracts a human-readable message from a failed request.
 *
 * @param fallback - shown when the error is not an axios error, or carries no usable detail.
 */
export function errorMessage(error: unknown, fallback = "An error occurred"): string {
  if (!axios.isAxiosError(error)) return fallback
  const detail = error.response?.data?.detail
  if (typeof detail === "string") return detail || fallback
  // Routes that raise a structured detail (ytdl_router's 409s, for one) put the
  // human-readable text under `message`; the object itself renders as
  // "[object Object]". FastAPI's own 422 detail is an array and has no message,
  // so it lands on the fallback.
  if (detail && typeof detail === "object" && typeof detail.message === "string") {
    return detail.message
  }
  return fallback
}

/** Below 3 characters the backend's keyword search is noise, so send nothing. */
export const searchParam = (search: string | null | undefined): string | null =>
  search && search.length > 2 ? search : null

/** GET a paginated list endpoint. Callers add their own `.catch`. */
export async function fetchPage<T = unknown>(
  path: string,
  params: Record<string, unknown>
): Promise<{ pageCount: number; tableRows: T[] }> {
  const response = await axios.get(apiUrl(path), { params })
  return { pageCount: response.data.page_count, tableRows: response.data.records }
}

/**
 * Downloads a file from an API path to the user's disk.
 *
 * Cookies are sent automatically (axios.defaults.withCredentials = true), and the
 * 401 interceptor above still applies. The caller supplies the filename; blob: URLs
 * are same-origin, so the `download` attribute is honored even when the API lives on
 * another origin (dev). Note: the response is buffered fully in memory before saving.
 */
export async function downloadBlob(path: string, filename: string): Promise<void> {
  const response = await axios.get(apiUrl(path), { responseType: "blob" })
  const url = window.URL.createObjectURL(response.data)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.URL.revokeObjectURL(url)
}
