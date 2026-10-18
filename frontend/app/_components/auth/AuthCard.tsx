"use client"

import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

/** The full-page auth shell: sign-in, first-run setup, forced change, pending approval. */
export function AuthCard({
  subtitle,
  note,
  centered = false,
  children,
}: {
  subtitle?: string
  note?: ReactNode
  /** For the message-only screens, which have no form to left-align. */
  centered?: boolean
  children: ReactNode
}) {
  return (
    <div className="min-h-screen bg-bg-void bg-grid flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div
          className={cn(
            "border border-border rounded-lg bg-bg-terminal/80 backdrop-blur-sm p-8",
            centered && "text-center"
          )}
        >
          <div className={cn("text-center", subtitle ? "mb-8" : "mb-4")}>
            <h1 className="text-2xl font-mono font-bold text-matrix">yt-hoarder</h1>
            {subtitle && <p className="text-text-secondary text-sm mt-2 font-mono">{subtitle}</p>}
            {note && <p className="text-text-muted text-xs mt-1 font-mono">{note}</p>}
          </div>
          {children}
        </div>
      </div>
    </div>
  )
}
