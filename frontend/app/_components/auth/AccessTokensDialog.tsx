"use client"

import { useCallback, useState } from "react"
import axios from "axios"
import toast from "react-hot-toast"
import { ClipboardDocumentIcon, TrashIcon } from "@heroicons/react/24/outline"
import { apiUrl, errorMessage } from "@/app/lib/api"
import { copyToClipboard } from "@/app/lib/clipboard"
import { claudeCodeCommand, mcpServerUrl, mcpServersJson } from "@/app/lib/mcpConnection"
import { useFetchEffect } from "@/app/_hooks/useFetchEffect"
import { formatDate, formatRelativeTime } from "@/app/utils"
import { cn } from "@/lib/utils"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { LoadingSpinner } from "../LoadingSpinner"

export type AccessToken = {
  id: number
  name: string
  token_prefix: string
  created_at: string
  last_used_at: string | null
  expires_at: string | null
  status: "active" | "expired" | "invalidated" | "blocked"
}

type CreatedToken = AccessToken & { token: string }

const EXPIRY_CHOICES: { label: string; days: number | null }[] = [
  { label: "Never", days: null },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "1 year", days: 365 },
]

const STATUS_LABEL: Record<AccessToken["status"], { label: string; title: string }> = {
  active: { label: "active", title: "Agents can use this token" },
  expired: { label: "expired", title: "Past its expiry date" },
  invalidated: {
    label: "invalidated",
    title: "Stopped working when your password changed. Create a new one.",
  },
  blocked: { label: "blocked", title: "Your account cannot use tokens right now" },
}

function CopyRow({ label, value, multiline = false }: { label: string; value: string; multiline?: boolean }) {
  const copy = async () => {
    if (await copyToClipboard(value)) {
      toast.success(`${label} copied`)
    } else {
      toast.error("Could not copy — select the text and copy it manually")
    }
  }
  return (
    <div className="space-y-1">
      <div className="text-xs font-mono text-text-muted">{label}</div>
      <div className="flex items-start gap-2">
        <code
          className={cn(
            // wrap-anywhere, not break-all: a long token breaks only where it can't fit.
            "flex-1 min-w-0 px-3 py-2 bg-bg-void border border-border rounded font-mono text-xs text-text-primary select-all wrap-anywhere",
            multiline && "whitespace-pre-wrap"
          )}
        >
          {value}
        </code>
        <Button variant="outline" size="icon" onClick={copy} title={`Copy ${label.toLowerCase()}`}>
          <ClipboardDocumentIcon className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}

type AccessTokensDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * Personal access tokens for the MCP endpoint, which lets the user's own AI agent
 * (Claude Code, LM Studio, Cursor…) search their transcripts as them.
 *
 * Opened from the account menu rather than Settings: Settings is admin-only, and every
 * approved user can create tokens for themselves.
 */
export function AccessTokensDialog({ open, onOpenChange }: AccessTokensDialogProps) {
  const [tokens, setTokens] = useState<AccessToken[]>([])
  const [name, setName] = useState("")
  const [expiryDays, setExpiryDays] = useState<number | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [created, setCreated] = useState<CreatedToken | null>(null)

  const loadTokens = useCallback(async (signal: AbortSignal) => {
    try {
      const resp = await axios.get<AccessToken[]>(apiUrl("/auth/tokens"), { signal })
      setTokens(resp.data)
    } catch (error) {
      if (!axios.isCancel(error)) toast.error(errorMessage(error, "Failed to load tokens"))
    }
  }, [])
  const { isLoading, refetch } = useFetchEffect(loadTokens, [loadTokens], {
    enabled: open,
    initialLoading: true,
  })

  const serverUrl = typeof window === "undefined" ? "" : mcpServerUrl(apiUrl("/mcp"), window.location.origin)

  const close = () => {
    onOpenChange(false)
    // The plaintext must not survive the dialog: reopening shows the list, never the token.
    setCreated(null)
    setName("")
    setExpiryDays(null)
  }

  const createToken = async () => {
    setIsCreating(true)
    try {
      const resp = await axios.post<CreatedToken>(apiUrl("/auth/tokens"), {
        name: name.trim(),
        expires_in_days: expiryDays,
      })
      setCreated(resp.data)
      setName("")
      refetch()
    } catch (error) {
      toast.error(errorMessage(error, "Failed to create token"))
    } finally {
      setIsCreating(false)
    }
  }

  const revokeToken = async (token: AccessToken) => {
    if (!window.confirm(`Revoke "${token.name}"? Agents using it stop working immediately.`)) return
    try {
      await axios.delete(apiUrl(`/auth/tokens/${token.id}`))
      toast.success(`Revoked "${token.name}"`)
      refetch()
    } catch (error) {
      toast.error(errorMessage(error, "Failed to revoke token"))
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (isCreating) return
        if (!next) close()
      }}
    >
      <DialogContent className="sm:max-w-3xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{created ? "New access token" : "MCP access tokens"}</DialogTitle>
          <DialogDescription>
            {created
              ? "Shown once. Paste it into your agent now; it can't be displayed again."
              : "Let your own AI agent (Claude Code, LM Studio, Cursor…) search your transcripts. A token acts as you and sees exactly what you see."}
          </DialogDescription>
        </DialogHeader>

        {created ? (
          <div className="space-y-4 py-2">
            <CopyRow label="Token" value={created.token} />
            <CopyRow label="Claude Code" value={claudeCodeCommand(serverUrl, created.token)} />
            <CopyRow
              label="mcp.json (LM Studio, Cursor, …)"
              value={mcpServersJson(serverUrl, created.token)}
              multiline
            />
            <p className="text-xs text-text-muted">
              Clients store this header in plain text (Claude Code keeps it in{" "}
              <code className="font-mono">~/.claude.json</code>). Revoke the token here if that
              file leaks. Changing your password also revokes every token.
            </p>
          </div>
        ) : (
          <div className="space-y-5 py-2">
            <CopyRow label="Server URL" value={serverUrl} />

            <div className="space-y-2">
              <div className="text-xs font-mono text-text-muted">New token</div>
              <div className="flex flex-col sm:flex-row gap-2">
                <Input
                  placeholder="Name, e.g. laptop-claude"
                  value={name}
                  maxLength={64}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && name.trim()) createToken()
                  }}
                  className="sm:flex-1"
                />
                <Button
                  variant="matrix"
                  onClick={createToken}
                  disabled={isCreating || !name.trim()}
                >
                  {isCreating ? (
                    <>
                      <LoadingSpinner />
                      Creating...
                    </>
                  ) : (
                    "Create"
                  )}
                </Button>
              </div>
              <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Expires">
                <span className="text-xs text-text-muted mr-1">Expires:</span>
                {EXPIRY_CHOICES.map((choice) => (
                  <button
                    key={choice.label}
                    type="button"
                    aria-pressed={expiryDays === choice.days}
                    onClick={() => setExpiryDays(choice.days)}
                    className={cn(
                      "px-2 py-0.5 text-xs font-mono rounded border transition-colors",
                      expiryDays === choice.days
                        ? "bg-matrix/20 border-matrix/30 text-matrix"
                        : "border-border text-text-muted hover:text-text-secondary"
                    )}
                  >
                    {choice.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <div className="text-xs font-mono text-text-muted">Your tokens</div>
              {isLoading && tokens.length === 0 ? (
                <div className="flex justify-center py-4">
                  <LoadingSpinner />
                </div>
              ) : tokens.length === 0 ? (
                <p className="text-sm text-text-muted">No tokens yet.</p>
              ) : (
                <ul className="divide-y divide-border border border-border rounded">
                  {tokens.map((token) => (
                    <li key={token.id} className="flex items-center gap-3 px-3 py-2">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-sm text-text-primary truncate">
                            {token.name}
                          </span>
                          <Badge
                            variant={token.status === "active" ? "success" : "warning"}
                            title={STATUS_LABEL[token.status].title}
                          >
                            {STATUS_LABEL[token.status].label}
                          </Badge>
                        </div>
                        <div className="text-xs text-text-muted font-mono truncate">
                          {token.token_prefix}… · created {formatDate(token.created_at)} · used{" "}
                          {token.last_used_at ? formatRelativeTime(token.last_used_at) : "never"}
                          {token.expires_at && ` · expires ${formatDate(token.expires_at)}`}
                        </div>
                      </div>
                      <button
                        onClick={() => revokeToken(token)}
                        className="p-1 text-text-muted hover:text-status-error transition-colors rounded"
                        title={`Revoke ${token.name}`}
                      >
                        <TrashIcon className="h-4 w-4" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}

        <DialogFooter>
          {created ? (
            <Button variant="matrix" onClick={() => setCreated(null)}>
              Done
            </Button>
          ) : (
            <Button variant="outline" onClick={close}>
              Close
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
