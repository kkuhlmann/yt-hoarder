"use client"

import { Button } from "@/components/ui/button"
import { LoadingSpinner } from "./LoadingSpinner"
import { XMarkIcon } from "@heroicons/react/20/solid"
import { motion, AnimatePresence } from "framer-motion"

export type BulkBarButton = {
  key: string
  onClick: () => void
  variant: "destructive" | "secondary" | "matrix"
  disabled?: boolean
  title?: string
  isLoading?: boolean
  loadingLabel: string
  content: React.ReactNode
}

/**
 * Shell shared by every bulk-actions bar: the collapse animation, the
 * "N selected" counter with its clear button, and the button row.
 *
 * Renders nothing at `count === 0`, so callers mount it unconditionally — gating
 * it from outside unmounts the AnimatePresence and loses the exit animation.
 */
export function BulkBar({
  count,
  onClearSelection,
  isLoading,
  buttons,
}: {
  count: number
  onClearSelection: () => void
  isLoading: boolean
  buttons: BulkBarButton[]
}) {
  return (
    <AnimatePresence>
      {count > 0 && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="overflow-hidden"
        >
          <div className="flex flex-wrap items-center gap-3 p-3 bg-bg-surface border border-border rounded-lg">
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm text-matrix">{count} selected</span>
              <button
                onClick={onClearSelection}
                className="p-1 hover:bg-bg-elevated rounded transition-colors"
                title="Clear selection"
                disabled={isLoading}
              >
                <XMarkIcon className="h-4 w-4 text-text-muted hover:text-text-primary" />
              </button>
            </div>

            <div className="flex flex-wrap gap-2">
              {buttons.map((b) => (
                <Button
                  key={b.key}
                  size="sm"
                  variant={b.variant}
                  onClick={b.onClick}
                  disabled={b.disabled || isLoading}
                  title={b.title}
                  className="gap-1"
                >
                  {b.isLoading ? (
                    <>
                      <LoadingSpinner />
                      {b.loadingLabel}
                    </>
                  ) : (
                    b.content
                  )}
                </Button>
              ))}
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
