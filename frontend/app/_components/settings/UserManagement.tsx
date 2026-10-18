"use client"

import { useState, useCallback } from "react"
import { useFetchEffect } from "@/app/_hooks/useFetchEffect"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { ArrowPathIcon, CheckIcon, TrashIcon, PencilIcon, KeyIcon, XMarkIcon, NoSymbolIcon } from "@heroicons/react/24/outline"
import axios from "axios"
import toast from "react-hot-toast"
import { apiUrl, errorMessage } from "@/app/lib/api"
import { useAuth } from "@/app/context/AuthContext"
import { cn } from "@/lib/utils"
import { formatBytes } from "@/app/utils"
import { ResetPasswordDialog } from "./ResetPasswordDialog"

type ManagedUser = {
  id: number
  username: string
  is_admin: boolean
  is_approved: boolean
  created_at: string
  media_count: number
  storage_limit_bytes: number | null
  storage_used_bytes: number
  password_reset_requested_at: string | null
  must_change_password: boolean
  token_count: number
}

const STORAGE_PRESETS = [
  { label: "10 GB", bytes: 10 * 1024 ** 3 },
  { label: "50 GB", bytes: 50 * 1024 ** 3 },
  { label: "100 GB", bytes: 100 * 1024 ** 3 },
  { label: "500 GB", bytes: 500 * 1024 ** 3 },
  { label: "1 TB", bytes: 1024 ** 4 },
  { label: "Unlimited", bytes: null as number | null },
]

export function UserManagement() {
  const { user: currentUser } = useAuth()
  const [users, setUsers] = useState<ManagedUser[]>([])
  const [editingStorageUserId, setEditingStorageUserId] = useState<number | null>(null)
  const [storageInputGB, setStorageInputGB] = useState("")
  const [resetTarget, setResetTarget] = useState<ManagedUser | null>(null)

  const fetchUsers = useCallback(async () => {
    try {
      const response = await axios.get(apiUrl("/auth/users"))
      setUsers(response.data)
    } catch (error) {
      console.error("Failed to fetch users:", error)
      toast.error("Failed to load users")
    }
  }, [])

  // Drives the initial load only. The handlers below keep calling fetchUsers()
  // directly rather than refetch(), because `loading` gates a full-component
  // spinner — routing an after-approve refresh through it would blank the list.
  const { isLoading: loading } = useFetchEffect(fetchUsers, [fetchUsers], {
    initialLoading: true,
  })

  const approveUser = async (userId: number) => {
    try {
      await axios.post(apiUrl(`/auth/users/${userId}/approve`))
      toast.success("User approved")
      fetchUsers()
    } catch (error) {
      toast.error(errorMessage(error, "Failed to approve user"))
    }
  }

  const deleteUser = async (userId: number, username: string) => {
    if (!window.confirm(`Are you sure you want to delete user "${username}"? This cannot be undone.`)) {
      return
    }
    try {
      await axios.delete(apiUrl(`/auth/users/${userId}`))
      toast.success(`User "${username}" deleted`)
      fetchUsers()
    } catch (error) {
      toast.error(errorMessage(error, "Failed to delete user"))
    }
  }

  const revokeTokens = async (userId: number, username: string) => {
    if (!window.confirm(`Revoke every MCP access token "${username}" holds? Their agents stop working immediately.`)) {
      return
    }
    try {
      const response = await axios.delete(apiUrl(`/auth/tokens/users/${userId}`))
      toast.success(`Revoked ${response.data.revoked} token(s) for "${username}"`)
      fetchUsers()
    } catch (error) {
      toast.error(errorMessage(error, "Failed to revoke tokens"))
    }
  }

  const dismissResetRequest = async (userId: number) => {
    try {
      await axios.delete(apiUrl(`/auth/users/${userId}/reset-request`))
      toast.success("Reset request dismissed")
      fetchUsers()
    } catch (error) {
      toast.error(errorMessage(error, "Failed to dismiss request"))
    }
  }

  const setStorageLimit = async (userId: number, limitBytes: number | null) => {
    try {
      await axios.put(apiUrl(`/auth/users/${userId}/storage-limit`), {
        storage_limit_bytes: limitBytes,
      })
      toast.success(limitBytes ? `Storage limit set to ${formatBytes(limitBytes)}` : "Storage limit removed (unlimited)")
      setEditingStorageUserId(null)
      setStorageInputGB("")
      fetchUsers()
    } catch (error) {
      toast.error(errorMessage(error, "Failed to set storage limit"))
    }
  }

  const handleStorageSubmit = (userId: number) => {
    const gb = parseFloat(storageInputGB)
    if (isNaN(gb) || gb <= 0) {
      toast.error("Enter a valid number of GB")
      return
    }
    setStorageLimit(userId, Math.round(gb * 1024 ** 3))
  }

  const pendingUsers = users.filter((u) => !u.is_approved)
  const approvedUsers = users.filter((u) => u.is_approved)
  const resetRequests = users.filter((u) => u.password_reset_requested_at)

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-text-muted py-4">
        <ArrowPathIcon className="h-4 w-4 animate-spin" />
        <span className="font-mono text-sm">Loading users...</span>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {pendingUsers.length > 0 && (
        <div className="border border-status-warning/30 rounded-lg p-4 bg-status-warning/5">
          <h4 className="font-mono text-sm font-medium text-status-warning mb-3">
            Pending Approval ({pendingUsers.length})
          </h4>
          <div className="space-y-2">
            {pendingUsers.map((user) => (
              <div
                key={user.id}
                className="flex items-center justify-between py-2 px-3 bg-bg-surface rounded border border-border/50"
              >
                <div>
                  <span className="font-mono text-sm">{user.username}</span>
                  <span className="text-xs text-text-muted ml-2">
                    {user.created_at ? new Date(user.created_at).toLocaleDateString() : ""}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => approveUser(user.id)}
                    className="flex items-center gap-1 px-2 py-1 text-xs font-mono bg-matrix/20 border border-matrix/30 text-matrix rounded hover:bg-matrix/30 transition-colors"
                  >
                    <CheckIcon className="h-3 w-3" />
                    Approve
                  </button>
                  <button
                    onClick={() => deleteUser(user.id, user.username)}
                    className="flex items-center gap-1 px-2 py-1 text-xs font-mono text-status-error hover:bg-status-error/20 rounded transition-colors"
                  >
                    <TrashIcon className="h-3 w-3" />
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {resetRequests.length > 0 && (
        <div className="border border-matrix/30 rounded-lg p-4 bg-matrix/5">
          <h4 className="font-mono text-sm font-medium text-matrix mb-1">
            Password Reset Requests ({resetRequests.length})
          </h4>
          <p className="text-xs text-text-muted mb-3">
            There is no email integration — hand the temporary password over yourself.
          </p>
          <div className="space-y-2">
            {resetRequests.map((user) => (
              <div
                key={user.id}
                className="flex items-center justify-between py-2 px-3 bg-bg-surface rounded border border-border/50"
              >
                <div>
                  <span className="font-mono text-sm">{user.username}</span>
                  <span className="text-xs text-text-muted ml-2">
                    asked {new Date(user.password_reset_requested_at!).toLocaleString()}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setResetTarget(user)}
                    className="flex items-center gap-1 px-2 py-1 text-xs font-mono bg-matrix/20 border border-matrix/30 text-matrix rounded hover:bg-matrix/30 transition-colors"
                  >
                    <KeyIcon className="h-3 w-3" />
                    Reset
                  </button>
                  <button
                    onClick={() => dismissResetRequest(user.id)}
                    className="flex items-center gap-1 px-2 py-1 text-xs font-mono text-text-muted hover:text-text-primary hover:bg-border/30 rounded transition-colors"
                    title="Clear the request without resetting"
                  >
                    <XMarkIcon className="h-3 w-3" />
                    Dismiss
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-2">
        {approvedUsers.map((user) => {
          const usagePercent = user.storage_limit_bytes
            ? Math.min(100, (user.storage_used_bytes / user.storage_limit_bytes) * 100)
            : 0
          const isEditing = editingStorageUserId === user.id
          const isOverWarning = usagePercent > 80
          const isCritical = usagePercent > 95

          return (
            <div
              key={user.id}
              className="py-2 px-3 border-b border-border/50 last:border-0"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm">{user.username}</span>
                  {user.is_admin && (
                    <Badge variant="outline" className="text-xs">admin</Badge>
                  )}
                  {user.must_change_password && (
                    <Badge variant="outline" className="text-xs text-status-warning border-status-warning/40">
                      temp password
                    </Badge>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-text-muted font-mono">
                    {user.media_count} media
                  </span>
                  <span
                    className={cn(
                      "text-xs font-mono",
                      isCritical ? "text-status-error" :
                      isOverWarning ? "text-status-warning" :
                      "text-text-muted"
                    )}
                    title={`${user.storage_used_bytes.toLocaleString()} bytes used${user.storage_limit_bytes ? ` / ${user.storage_limit_bytes.toLocaleString()} bytes limit` : ""}`}
                  >
                    {formatBytes(user.storage_used_bytes)}
                    {" / "}
                    {user.storage_limit_bytes ? formatBytes(user.storage_limit_bytes) : "Unlimited"}
                  </span>
                  <button
                    onClick={() => {
                      if (isEditing) {
                        setEditingStorageUserId(null)
                        setStorageInputGB("")
                      } else {
                        setEditingStorageUserId(user.id)
                        setStorageInputGB(
                          user.storage_limit_bytes
                            ? (user.storage_limit_bytes / 1024 ** 3).toFixed(0)
                            : ""
                        )
                      }
                    }}
                    className="p-1 text-text-muted hover:text-matrix transition-colors rounded"
                    title="Edit storage limit"
                  >
                    <PencilIcon className="h-3.5 w-3.5" />
                  </button>
                  {user.token_count > 0 && (
                    <button
                      onClick={() => revokeTokens(user.id, user.username)}
                      className="p-1 text-text-muted hover:text-status-error transition-colors rounded"
                      title={`Revoke ${user.token_count} MCP access token(s)`}
                    >
                      <NoSymbolIcon className="h-3.5 w-3.5" />
                    </button>
                  )}
                  {user.id !== currentUser?.id && (
                    <button
                      onClick={() => setResetTarget(user)}
                      className="p-1 text-text-muted hover:text-matrix transition-colors rounded"
                      title="Reset password"
                    >
                      <KeyIcon className="h-3.5 w-3.5" />
                    </button>
                  )}
                  {!user.is_admin && (
                    <button
                      onClick={() => deleteUser(user.id, user.username)}
                      className="p-1 text-text-muted hover:text-status-error transition-colors rounded"
                      title="Delete user"
                    >
                      <TrashIcon className="h-4 w-4" />
                    </button>
                  )}
                </div>
              </div>
              {user.storage_limit_bytes && (
                <div className="mt-1.5 h-1 rounded-full bg-border/50 overflow-hidden">
                  <div
                    className={cn(
                      "h-full rounded-full transition-all",
                      isCritical ? "bg-status-error" :
                      isOverWarning ? "bg-status-warning" :
                      "bg-matrix"
                    )}
                    style={{ width: `${usagePercent}%` }}
                  />
                </div>
              )}
              {isEditing && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <div className="flex items-center gap-1">
                    <Input
                      type="number"
                      placeholder="GB"
                      value={storageInputGB}
                      onChange={(e) => setStorageInputGB(e.target.value)}
                      className="w-20 h-7 text-xs font-mono"
                      min={1}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleStorageSubmit(user.id)
                        if (e.key === "Escape") {
                          setEditingStorageUserId(null)
                          setStorageInputGB("")
                        }
                      }}
                    />
                    <span className="text-xs text-text-muted">GB</span>
                    <button
                      onClick={() => handleStorageSubmit(user.id)}
                      className="px-2 py-1 text-xs font-mono bg-matrix/20 border border-matrix/30 text-matrix rounded hover:bg-matrix/30 transition-colors"
                    >
                      Set
                    </button>
                  </div>
                  <div className="flex items-center gap-1">
                    {STORAGE_PRESETS.map((preset) => (
                      <button
                        key={preset.label}
                        onClick={() => setStorageLimit(user.id, preset.bytes)}
                        className={cn(
                          "px-1.5 py-0.5 text-xs font-mono rounded transition-colors",
                          (preset.bytes === null && user.storage_limit_bytes === null) ||
                          preset.bytes === user.storage_limit_bytes
                            ? "bg-matrix/20 text-matrix border border-matrix/30"
                            : "text-text-muted hover:text-text-secondary hover:bg-border/30 border border-transparent"
                        )}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      <ResetPasswordDialog
        user={resetTarget}
        onOpenChange={(open) => !open && setResetTarget(null)}
        onReset={fetchUsers}
      />
    </div>
  )
}
