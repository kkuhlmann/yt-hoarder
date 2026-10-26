"use client"

import { Fragment, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { LoadingSpinner } from "./LoadingSpinner"

type ConfirmDialogProps = {
  open: boolean
  /** Handles X button, overlay click, and Escape. Suppressed while loading. */
  onOpenChange: (open: boolean) => void
  icon?: React.ReactNode
  title: React.ReactNode
  description: React.ReactNode
  descriptionClassName?: string
  /** Body slot: detail grids, extra option checkboxes, task lists, etc. */
  children?: React.ReactNode
  confirmLabel: React.ReactNode
  /** Shown next to the spinner while loading; falls back to confirmLabel. */
  loadingLabel?: React.ReactNode
  cancelLabel?: React.ReactNode
  confirmVariant?: "destructive" | "matrix"
  confirmDisabled?: boolean
  isLoading?: boolean
  onConfirm: () => void | Promise<void>
  /** Cancel button handler; defaults to closing via onOpenChange. */
  onCancel?: () => void
  contentClassName?: string
}

export function ConfirmDialog({
  open,
  onOpenChange,
  icon,
  title,
  description,
  descriptionClassName,
  children,
  confirmLabel,
  loadingLabel,
  cancelLabel = "Cancel",
  confirmVariant = "destructive",
  confirmDisabled = false,
  isLoading = false,
  onConfirm,
  onCancel,
  contentClassName = "sm:max-w-md",
}: ConfirmDialogProps) {
  const [pending, setPending] = useState(false)
  const loading = pending || isLoading

  const handleConfirm = async () => {
    const result = onConfirm()
    if (result instanceof Promise) {
      setPending(true)
      try {
        await result
      } finally {
        setPending(false)
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={loading ? undefined : onOpenChange}>
      <DialogContent className={contentClassName}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {icon}
            {title}
          </DialogTitle>
          <DialogDescription className={descriptionClassName}>
            {description}
          </DialogDescription>
        </DialogHeader>

        {children}

        <DialogFooter className="gap-2 sm:gap-0">
          <Button
            variant="secondary"
            onClick={onCancel ?? (() => onOpenChange(false))}
            disabled={loading}
          >
            {cancelLabel}
          </Button>
          <Button
            variant={confirmVariant}
            onClick={handleConfirm}
            disabled={loading || confirmDisabled}
          >
            {loading ? (
              <>
                <LoadingSpinner />
                {loadingLabel ?? confirmLabel}
              </>
            ) : (
              confirmLabel
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

type DetailRow = {
  label: string
  value: React.ReactNode
  valueClassName?: string
}

export function ConfirmDetailGrid({ rows }: { rows: DetailRow[] }) {
  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
      {rows.map(({ label, value, valueClassName }) => (
        <Fragment key={label}>
          <span className="text-text-muted font-mono">{label}</span>
          <span className={cn("min-w-0 wrap-anywhere", valueClassName ?? "text-text-primary")}>
            {value}
          </span>
        </Fragment>
      ))}
    </div>
  )
}

type KeepTranscriptsCheckboxProps = {
  /** Unique DOM id — two confirm dialogs can be mounted at once. */
  id: string
  checked: boolean
  onChange: (checked: boolean) => void
}

export function KeepTranscriptsCheckbox({
  id,
  checked,
  onChange,
}: KeepTranscriptsCheckboxProps) {
  return (
    <div className="flex items-center gap-2 py-2">
      <input
        type="checkbox"
        id={id}
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-border accent-matrix"
      />
      <label htmlFor={id} className="text-sm text-text-secondary cursor-pointer">
        Keep transcripts (remain searchable after deletion)
      </label>
    </div>
  )
}

export function getBasename(filePath: string | undefined) {
  if (!filePath) return "N/A"
  return filePath.split("/").pop() || filePath
}
