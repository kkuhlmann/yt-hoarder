"use client"

import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from "react"
import axios from "axios"
import { apiUrl } from "@/app/lib/api"
import { useFetchEffect } from "@/app/_hooks/useFetchEffect"
import { useOffline } from "@/app/context/OfflineContext"

type User = {
  id: number
  username: string
  is_admin: boolean
  is_approved: boolean
  must_change_password: boolean
  geo_background_preset: string | null
  has_geo_background: boolean
  /** Instance-wide: whether the MCP endpoint is on, so token management is offered. */
  mcp_enabled?: boolean
}

type AuthState = {
  user: User | null
  isLoading: boolean
  needsSetup: boolean
}

type AuthContextType = AuthState & {
  login: (username: string, password: string) => Promise<void>
  register: (username: string, password: string) => Promise<{ is_approved: boolean }>
  logout: () => Promise<void>
  refreshAuth: () => Promise<void>
  /** The temp password from this session's login, if it forced a password change. */
  pendingTempPassword: string | null
  clearPendingTempPassword: () => void
}

const AuthContext = createContext<AuthContextType | null>(null)

/**
 * The last identity seen online, so offline mode has someone to be.
 *
 * This is a local-device convenience, not an authorization decision: it unlocks
 * nothing but the media already downloaded to this phone, and every server-side
 * check still runs the moment the app is online again. It is stored unencrypted
 * like every other browser preference, so it holds no secret — the auth cookie
 * remains HttpOnly and is never copied here.
 */
const OFFLINE_USER_KEY = "offline:user"

function cacheOfflineUser(user: User) {
  // Not cached mid-forced-password-change: AuthGuard would then render
  // ForcePasswordChange offline, a screen that cannot possibly complete without
  // the server. Falling back to the login page is the honest outcome.
  if (user.must_change_password) return
  try {
    localStorage.setItem(OFFLINE_USER_KEY, JSON.stringify(user))
  } catch {
    // Storage unavailable; offline mode simply won't have a name to show.
  }
}

function readOfflineUser(): User | null {
  try {
    const raw = localStorage.getItem(OFFLINE_USER_KEY)
    return raw ? (JSON.parse(raw) as User) : null
  } catch {
    return null
  }
}

function clearOfflineUser() {
  try {
    localStorage.removeItem(OFFLINE_USER_KEY)
  } catch {
    // Nothing to do — the value is advisory.
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const { offlineMode } = useOffline()
  const [state, setState] = useState<AuthState>({
    user: null,
    isLoading: true,
    needsSetup: false,
  })
  const [pendingTempPassword, setPendingTempPassword] = useState<string | null>(null)

  const refreshAuth = useCallback(async () => {
    const MAX_RETRIES = 30
    const RETRY_DELAY_MS = 2000
    const REQUEST_TIMEOUT_MS = 5000

    // The retry loop below spends a full minute before giving up, which is right
    // for a server still booting and wrong for a phone in airplane mode. Offline
    // mode says the network is not coming, so restore the last identity and go.
    if (offlineMode) {
      setState({ user: readOfflineUser(), isLoading: false, needsSetup: false })
      return
    }

    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      try {
        const setupResp = await axios.get(apiUrl("/auth/setup-status"), {
          timeout: REQUEST_TIMEOUT_MS,
        })
        if (setupResp.data.needs_setup) {
          setState({ user: null, isLoading: false, needsSetup: true })
          return
        }

        const meResp = await axios.get(apiUrl("/auth/me"), {
          timeout: REQUEST_TIMEOUT_MS,
        })
        cacheOfflineUser(meResp.data)
        setState({ user: meResp.data, isLoading: false, needsSetup: false })
        return
      } catch (err) {
        const isTimeout = axios.isAxiosError(err) && err.code === "ECONNABORTED"
        const isNetworkError = axios.isAxiosError(err) && !err.response

        if ((isTimeout || isNetworkError) && attempt < MAX_RETRIES) {
          await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS))
          continue
        }

        // Non-retryable error (401, 403, etc.) or max retries exhausted
        setState({ user: null, isLoading: false, needsSetup: false })
        return
      }
    }
  }, [offlineMode])

  // Bootstrap on mount. The hook's own isLoading is unused here — `refreshAuth`
  // owns its retry loop and writes state.isLoading itself, which is what the
  // rest of the app reads.
  useFetchEffect(refreshAuth, [refreshAuth])

  // Listen for 401 events from the axios interceptor
  useEffect(() => {
    const handleUnauthorized = () => {
      setState((prev) => ({ ...prev, user: null }))
    }
    window.addEventListener("auth:unauthorized", handleUnauthorized)
    return () => window.removeEventListener("auth:unauthorized", handleUnauthorized)
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const resp = await axios.post(apiUrl("/auth/login"), { username, password })
    // Hold the temporary password just long enough to hand it to the forced-change
    // screen, so the user isn't asked to retype what they typed one screen ago. Kept in
    // memory only, never persisted, and cleared as soon as it's spent or the user leaves.
    // If it's absent (a reload, or a session that predates the reset) that screen falls
    // back to asking — the server always verifies the current password either way.
    setPendingTempPassword(resp.data.must_change_password ? password : null)
    cacheOfflineUser(resp.data)
    setState({ user: resp.data, isLoading: false, needsSetup: false })
  }, [])

  const register = useCallback(async (username: string, password: string) => {
    const resp = await axios.post(apiUrl("/auth/register"), { username, password })
    const data = resp.data
    // First user gets auto-logged-in (cookie set by backend)
    if (data.is_approved) {
      setState({ user: data, isLoading: false, needsSetup: false })
    }
    return { is_approved: data.is_approved }
  }, [])

  const logout = useCallback(async () => {
    await axios.post(apiUrl("/auth/logout"))
    clearOfflineUser()
    setPendingTempPassword(null)
    setState({ user: null, isLoading: false, needsSetup: false })
  }, [])

  const clearPendingTempPassword = useCallback(() => setPendingTempPassword(null), [])

  return (
    <AuthContext.Provider
      value={{
        ...state,
        login,
        register,
        logout,
        refreshAuth,
        pendingTempPassword,
        clearPendingTempPassword,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider")
  }
  return context
}
