"use client"

import { useCallback, useState } from "react"
import { TrashIcon } from "@heroicons/react/24/outline"
import toast from "react-hot-toast"

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { useFetchEffect } from "@/app/_hooks/useFetchEffect"
import { useOfflineDownloads } from "@/app/_hooks/useOfflineDownloads"
import { useOfflineSupported } from "@/app/_hooks/useOfflineSupported"
import { useStoredValue, writeStored } from "@/app/_hooks/useStoredValue"
import { allItems, allMeta, storedBytesOf, type OfflineItem } from "@/app/lib/offlineDb"
import { BUDGET_STORAGE_KEY, readBudgetBytes } from "@/app/lib/offlineBudget"
import { removeDownload, resumeInterrupted } from "@/app/lib/offlineDownloader"
import { formatBytes } from "@/app/utils"

const GIB = 1024 * 1024 * 1024
const BUDGET_CHOICES_GIB = [1, 2, 4, 8, 16, 32]

type Row = { item: OfflineItem; title: string }

/**
 * Storage management for the offline library: what is stored, and the cap on it.
 *
 * Controlled, like ChangePasswordDialog — the owner holds `open` so the dialog
 * survives the menu that opened it being dismissed.
 */
export function OfflineStorageDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const supported = useOfflineSupported()
  const [rows, setRows] = useState<Row[]>([])
  const [quota, setQuota] = useState<{ usage: number; quota: number } | null>(null)
  const budgetBytes = useStoredValue(readBudgetBytes, 4 * GIB)

  // Re-reads whenever a download finishes, so the list can't drift from the store.
  const downloads = useOfflineDownloads()

  const load = useCallback(async () => {
    const [items, metas] = await Promise.all([allItems(), allMeta()])
    const titles = new Map(
      metas.map((meta) => [meta.id, String(meta.record.title ?? `Media ${meta.id}`)])
    )
    setRows(
      items
        .sort((a, b) => b.addedAt - a.addedAt)
        .map((item) => ({ item, title: titles.get(item.id) ?? `Media ${item.id}` }))
    )
    try {
      const estimate = await navigator.storage?.estimate?.()
      setQuota(
        estimate ? { usage: estimate.usage ?? 0, quota: estimate.quota ?? 0 } : null
      )
    } catch {
      setQuota(null)
    }
  }, [])

  const { refetch } = useFetchEffect(load, [load, downloads], { enabled: open })

  const used = rows.reduce((total, row) => total + storedBytesOf(row.item), 0)
  const pinnedCount = rows.filter((row) => row.item.pinned).length

  const remove = async (id: number) => {
    await removeDownload(id)
    refetch()
  }

  const removeUnpinned = async () => {
    const unpinned = rows.filter((row) => !row.item.pinned)
    for (const row of unpinned) await removeDownload(row.item.id)
    toast.success(`Removed ${unpinned.length} synced item${unpinned.length === 1 ? "" : "s"}`)
    refetch()
  }

  if (!supported) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Offline storage</DialogTitle>
          <DialogDescription>
            {formatBytes(used)} of {formatBytes(budgetBytes)} used by {rows.length} item
            {rows.length === 1 ? "" : "s"}
            {quota && quota.quota > 0
              ? ` — this browser allows about ${formatBytes(quota.quota)}`
              : ""}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <p className="text-xs font-mono text-text-muted mb-2">Storage budget</p>
            <div className="flex flex-wrap gap-1">
              {BUDGET_CHOICES_GIB.map((gib) => (
                <button
                  key={gib}
                  type="button"
                  onClick={() => writeStored(BUDGET_STORAGE_KEY, String(gib * GIB))}
                  className={`px-2 py-1 text-xs font-mono rounded transition-colors ${
                    budgetBytes === gib * GIB
                      ? "bg-matrix/20 text-matrix"
                      : "text-text-muted hover:bg-bg-surface"
                  }`}
                >
                  {gib} GB
                </button>
              ))}
            </div>
            <p className="mt-2 text-xs font-mono text-text-muted/70">
              Items you downloaded by hand are kept. Playlist and subscription
              downloads are removed oldest-played-first to stay inside the budget.
            </p>
          </div>

          <div className="max-h-64 overflow-y-auto divide-y divide-border">
            {rows.length === 0 && (
              <p className="py-6 text-center text-xs font-mono text-text-muted">
                Nothing downloaded yet.
              </p>
            )}
            {rows.map(({ item, title }) => (
              <div key={item.id} className="flex items-center gap-2 py-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-mono text-text-secondary">{title}</p>
                  <p className="text-[10px] font-mono text-text-muted">
                    {formatBytes(storedBytesOf(item))}
                    {item.pinned ? " · kept" : " · synced"}
                    {item.complete ? "" : " · incomplete"}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void remove(item.id)}
                  title="Remove from this device"
                  className="p-1 rounded hover:bg-status-error/20 transition-colors"
                >
                  <TrashIcon className="h-4 w-4 text-text-muted hover:text-status-error" />
                </button>
              </div>
            ))}
          </div>

          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void resumeInterrupted().then(refetch)}
              disabled={rows.every((row) => row.item.complete)}
            >
              Resume interrupted
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void removeUnpinned()}
              disabled={rows.length === pinnedCount}
            >
              Remove synced items
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
