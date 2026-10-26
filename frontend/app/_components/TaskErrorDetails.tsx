"use client"

import { useState } from "react"
import toast from "react-hot-toast"
import {
  ClipboardIcon,
  CheckIcon,
  ArrowTopRightOnSquareIcon,
} from "@heroicons/react/24/outline"
import { copyToClipboard } from "@/app/lib/clipboard"

type TaskErrorDetailsProps = {
  url: string | null
  message: string | null
  /** Section label for the message box. Defaults to "Error". */
  label?: string
}

/**
 * Shared block showing a task's download URL (clickable + copyable) and its full
 * status/error message (scrollable + copyable). Rendered inside the task action
 * dialogs so failures can be diagnosed without hovering the truncated table cell.
 */
export function TaskErrorDetails({ url, message, label = "Error" }: TaskErrorDetailsProps) {
  const [copied, setCopied] = useState<"url" | "message" | null>(null)

  if (!url && !message) return null

  const handleCopy = async (text: string, which: "url" | "message", successMessage: string) => {
    if (!(await copyToClipboard(text))) {
      toast.error("Failed to copy")
      return
    }
    setCopied(which)
    toast.success(successMessage)
    setTimeout(() => setCopied((prev) => (prev === which ? null : prev)), 2000)
  }

  return (
    <div className="space-y-3 text-sm">
      {url && (
        <div className="flex items-start gap-2 min-w-0">
          <span className="text-text-muted font-mono shrink-0">URL:</span>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            title={url}
            className="text-matrix hover:underline wrap-anywhere min-w-0 flex-1"
          >
            {url}
          </a>
          <button
            type="button"
            onClick={() => handleCopy(url, "url", "URL copied")}
            title="Copy URL"
            className="text-text-muted hover:text-text-primary transition-colors shrink-0"
          >
            {copied === "url" ? (
              <CheckIcon className="h-4 w-4 text-matrix" />
            ) : (
              <ClipboardIcon className="h-4 w-4" />
            )}
          </button>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            title="Open in new tab"
            className="text-text-muted hover:text-text-primary transition-colors shrink-0"
          >
            <ArrowTopRightOnSquareIcon className="h-4 w-4" />
          </a>
        </div>
      )}

      {message && (
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-text-muted font-mono">{label}</span>
            <button
              type="button"
              onClick={() => handleCopy(message, "message", `${label} copied`)}
              title={`Copy ${label.toLowerCase()}`}
              className="text-text-muted hover:text-text-primary transition-colors"
            >
              {copied === "message" ? (
                <CheckIcon className="h-4 w-4 text-matrix" />
              ) : (
                <ClipboardIcon className="h-4 w-4" />
              )}
            </button>
          </div>
          <div className="font-mono text-xs whitespace-pre-wrap wrap-break-word max-h-40 overflow-y-auto border border-border rounded bg-bg-surface p-2 text-text-secondary">
            {message}
          </div>
        </div>
      )}
    </div>
  )
}
