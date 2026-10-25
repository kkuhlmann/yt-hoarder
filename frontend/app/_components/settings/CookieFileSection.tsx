"use client"

import { useState, useCallback, useRef } from "react"
import { useFetchEffect } from "@/app/_hooks/useFetchEffect"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { OptionMeta } from "@/app/types/Settings"
import { ArrowPathIcon, ChevronUpIcon, ChevronDownIcon, TrashIcon, ArrowUpTrayIcon } from "@heroicons/react/24/outline"
import axios from "axios"
import toast from "react-hot-toast"
import { apiUrl, errorMessage } from "@/app/lib/api"
import { cn } from "@/lib/utils"
import { formatBytes } from "@/app/utils"

function formatCookieAge(uploadedAt: string): { text: string; isStale: boolean } {
  const uploaded = new Date(uploadedAt)
  const now = new Date()
  const diffMs = now.getTime() - uploaded.getTime()
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60))
  const diffDays = Math.floor(diffHours / 24)

  const isStale = diffDays >= 3

  if (diffHours < 1) return { text: "now", isStale: false }
  if (diffHours < 24) return { text: `${diffHours}h ago`, isStale }
  if (diffDays === 1) return { text: "1 day ago", isStale }
  return { text: `${diffDays} days ago`, isStale }
}

const COOKIES_MODE_OPTIONS = [
  { value: "ALWAYS", label: "Always", description: "Use cookies on every download" },
  { value: "RETRIES_ONLY", label: "Retries Only", description: "Use cookies only when retrying failed downloads" },
  { value: "NEVER", label: "Never", description: "Don't use cookies for downloads" },
] as const

const COOKIES_PLAYER_CLIENT_OPTIONS: OptionMeta[] = [
  { value: "web_embedded", label: "web_embedded", description: "Best for cookies, yt-dlp default" },
  { value: "tv_samsung", label: "tv_samsung", description: "No PO token needed, supports age-gate" },
  { value: "web", label: "web", description: "Browser cookies, supports age-gate" },
  { value: "web_safari", label: "web_safari", description: "Safari variant, supports age-gate" },
  { value: "mweb", label: "mweb", description: "Mobile web, supports age-gate" },
  { value: "web_creator", label: "web_creator", description: "Requires cookies, supports age-gate" },
  { value: "tv", label: "tv", description: "Supports age-gate, may have DRM" },
  { value: "web_music", label: "web_music", description: "YouTube Music" },
  { value: "visionos", label: "visionos", description: "No cookie support" },
  { value: "android_vr", label: "android_vr", description: "No cookie support; YouTube 403s its formats" },
  { value: "tv_downgraded", label: "tv_downgraded", description: "Broken in some sessions (page needs to be reloaded)" },
]

const COOKIES_PLAYER_CLIENT_MAP = new Map(COOKIES_PLAYER_CLIENT_OPTIONS.map((opt) => [opt.value, opt]))

export function CookieFileSection() {
  const [status, setStatus] = useState<{
    cookies_mode: string
    file_exists: boolean
    file_size_bytes: number
    uploaded_at: string | null
  } | null>(null)
  const [cookiesPlayerClient, setCookiesPlayerClient] = useState<string[]>([])
  const [uploading, setUploading] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [updatingMode, setUpdatingMode] = useState(false)
  const [updatingPlayerClient, setUpdatingPlayerClient] = useState(false)
  const [resettingPlayerClient, setResettingPlayerClient] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const response = await axios.get(apiUrl("/settings/cookies"))
      setStatus(response.data)
    } catch (error) {
      console.error("Failed to fetch cookie status:", error)
    }
  }, [])

  const fetchPlayerClient = useCallback(async () => {
    try {
      const response = await axios.get(apiUrl("/settings"))
      setCookiesPlayerClient(response.data.cookies_player_client)
    } catch (error) {
      console.error("Failed to fetch cookies player client:", error)
    }
  }, [])

  // Initial load. There is no loading flag here, and the handlers below keep
  // calling fetchStatus() directly, so nothing else needs the hook's return.
  useFetchEffect(
    () => Promise.all([fetchStatus(), fetchPlayerClient()]),
    [fetchStatus, fetchPlayerClient]
  )

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    setUploading(true)
    try {
      const formData = new FormData()
      formData.append("file", file)
      await axios.post(apiUrl("/settings/cookies"), formData, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      toast.success("Cookie file uploaded")
      fetchStatus()
    } catch (error) {
      toast.error(errorMessage(error, "Failed to upload cookie file"))
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = ""
      setUploading(false)
    }
  }

  const handleDelete = async () => {
    if (!window.confirm("Remove the cookie file? Downloads will no longer use cookie authentication.")) {
      return
    }
    setDeleting(true)
    try {
      await axios.delete(apiUrl("/settings/cookies"))
      toast.success("Cookie file removed")
      fetchStatus()
    } catch (error) {
      toast.error(errorMessage(error, "Failed to delete cookie file"))
    } finally {
      setDeleting(false)
    }
  }

  const saveCookiesPlayerClient = async (newList: string[]) => {
    setCookiesPlayerClient(newList)
    setUpdatingPlayerClient(true)
    try {
      await axios.put(apiUrl("/settings"), { cookies_player_client: newList })
    } catch (error) {
      toast.error(errorMessage(error, "Failed to update cookies player client"))
      fetchPlayerClient()
    } finally {
      setUpdatingPlayerClient(false)
    }
  }

  const toggleCookiesClient = (optionValue: string) => {
    if (cookiesPlayerClient.includes(optionValue)) {
      if (cookiesPlayerClient.length > 1) {
        saveCookiesPlayerClient(cookiesPlayerClient.filter((v) => v !== optionValue))
      }
    } else {
      saveCookiesPlayerClient([...cookiesPlayerClient, optionValue])
    }
  }

  const moveCookiesClientUp = (index: number) => {
    if (index <= 0) return
    const newOrder = [...cookiesPlayerClient]
    ;[newOrder[index - 1], newOrder[index]] = [newOrder[index], newOrder[index - 1]]
    saveCookiesPlayerClient(newOrder)
  }

  const moveCookiesClientDown = (index: number) => {
    if (index >= cookiesPlayerClient.length - 1) return
    const newOrder = [...cookiesPlayerClient]
    ;[newOrder[index], newOrder[index + 1]] = [newOrder[index + 1], newOrder[index]]
    saveCookiesPlayerClient(newOrder)
  }

  const resetCookiesPlayerClient = async () => {
    setResettingPlayerClient(true)
    try {
      await axios.put(apiUrl("/settings/reset/cookies_player_client"))
      const response = await axios.get(apiUrl("/settings"))
      setCookiesPlayerClient(response.data.cookies_player_client)
    } catch (error) {
      toast.error(errorMessage(error, "Failed to reset cookies player client"))
    } finally {
      setResettingPlayerClient(false)
    }
  }

  const handleModeChange = async (mode: string) => {
    if (mode === status?.cookies_mode) return
    setUpdatingMode(true)
    try {
      await axios.put(apiUrl("/settings"), { cookies_mode: mode })
      setStatus((prev) => prev ? { ...prev, cookies_mode: mode } : prev)
    } catch (error) {
      toast.error(errorMessage(error, "Failed to update cookie mode"))
    } finally {
      setUpdatingMode(false)
    }
  }

  const hasFile = status?.file_exists
  const cookieAge = hasFile && status?.uploaded_at ? formatCookieAge(status.uploaded_at) : null
  const currentModeDesc = COOKIES_MODE_OPTIONS.find((o) => o.value === status?.cookies_mode)?.description

  return (
    <div className="space-y-3 py-3">
      <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 border-b border-border/50 pb-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-medium">Cookie File</span>
            {hasFile && (
              <Badge
                variant="outline"
                className={cn(
                  "text-xs",
                  cookieAge?.isStale
                    ? "text-status-warning border-status-warning/30"
                    : "text-matrix border-matrix/30"
                )}
              >
                {cookieAge?.isStale ? "Stale" : "Active"}
              </Badge>
            )}
          </div>
          <p className="text-xs text-text-muted mt-1">
            {hasFile
              ? `${formatBytes(status!.file_size_bytes)} — uploaded ${cookieAge?.text ?? "unknown"}${cookieAge?.isStale ? " (consider re-uploading)" : ""}`
              : "Upload a Netscape-format cookies.txt exported from Firefox (Chrome cannot export valid cookies)"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt"
            onChange={handleUpload}
            className="hidden"
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
          >
            {uploading ? (
              <ArrowPathIcon className="h-4 w-4 animate-spin mr-1" />
            ) : (
              <ArrowUpTrayIcon className="h-4 w-4 mr-1" />
            )}
            {hasFile ? "Replace" : "Upload"}
          </Button>
          {hasFile && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleDelete}
              disabled={deleting}
              className="text-status-error hover:text-status-error"
            >
              {deleting ? (
                <ArrowPathIcon className="h-4 w-4 animate-spin" />
              ) : (
                <TrashIcon className="h-4 w-4" />
              )}
            </Button>
          )}
        </div>
      </div>
      {hasFile && (
        <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4">
          <div className="flex-1 min-w-0">
            <span className="font-mono text-sm font-medium">Cookie Usage</span>
            <p className="text-xs text-text-muted mt-1">{currentModeDesc}</p>
          </div>
          <div className="flex items-center gap-1">
            {COOKIES_MODE_OPTIONS.map((option) => (
              <Button
                key={option.value}
                variant={status?.cookies_mode === option.value ? "default" : "outline"}
                size="sm"
                onClick={() => handleModeChange(option.value)}
                disabled={updatingMode}
                className="text-xs"
              >
                {option.label}
              </Button>
            ))}
          </div>
        </div>
      )}
      {hasFile && status?.cookies_mode !== "NEVER" && cookiesPlayerClient.length > 0 && (
        <div className="flex flex-col gap-2 pt-3 border-t border-border/30">
          <div className="flex items-center justify-between">
            <div>
              <span className="font-mono text-sm font-medium">Player Client Order (with cookies)</span>
              <p className="text-xs text-text-muted mt-1">
                Player clients tried when cookies are used (fallback order)
              </p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={resetCookiesPlayerClient}
              disabled={resettingPlayerClient}
              title="Reset to default"
            >
              <ArrowPathIcon className={cn("h-4 w-4", resettingPlayerClient && "animate-spin")} />
            </Button>
          </div>
          <div className="flex flex-wrap gap-2 mt-1">
            {COOKIES_PLAYER_CLIENT_OPTIONS.map((option) => (
              <label
                key={option.value}
                className={cn(
                  "flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-mono transition-all cursor-pointer",
                  cookiesPlayerClient.includes(option.value)
                    ? "bg-matrix/10 border border-matrix/30 text-matrix"
                    : "bg-bg-surface border border-border text-text-secondary hover:border-border-hover"
                )}
                title={option.description}
              >
                <Checkbox
                  checked={cookiesPlayerClient.includes(option.value)}
                  onCheckedChange={() => toggleCookiesClient(option.value)}
                  disabled={updatingPlayerClient}
                />
                {option.label}
              </label>
            ))}
          </div>
          {cookiesPlayerClient.length > 1 && (
            <div className="mt-3 pt-3 border-t border-border/30">
              <span className="text-xs text-text-muted mb-2 block">
                Priority order (first = highest priority)
              </span>
              <div className="flex flex-col gap-1">
                {cookiesPlayerClient.map((optionValue, index) => {
                  const optMeta = COOKIES_PLAYER_CLIENT_MAP.get(optionValue)
                  return (
                    <div
                      key={optionValue}
                      className="flex items-center gap-2 px-2 py-1 bg-bg-surface rounded border border-border/50"
                    >
                      <div className="flex-1 min-w-0">
                        <span className="text-sm font-mono">
                          {index + 1}. {optMeta?.label || optionValue}
                        </span>
                        {optMeta?.description && (
                          <span className="text-xs text-text-muted ml-2">
                            — {optMeta.description}
                          </span>
                        )}
                      </div>
                      <button
                        onClick={() => moveCookiesClientUp(index)}
                        disabled={index === 0 || updatingPlayerClient}
                        className={cn(
                          "p-1 rounded hover:bg-bg-hover transition-colors",
                          index === 0 && "opacity-30 cursor-not-allowed"
                        )}
                        title="Move up"
                      >
                        <ChevronUpIcon className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => moveCookiesClientDown(index)}
                        disabled={index === cookiesPlayerClient.length - 1 || updatingPlayerClient}
                        className={cn(
                          "p-1 rounded hover:bg-bg-hover transition-colors",
                          index === cookiesPlayerClient.length - 1 && "opacity-30 cursor-not-allowed"
                        )}
                        title="Move down"
                      >
                        <ChevronDownIcon className="h-4 w-4" />
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
