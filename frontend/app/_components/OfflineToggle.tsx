"use client"

import { SignalIcon, SignalSlashIcon } from "@heroicons/react/24/outline"

import { useOffline } from "@/app/context/OfflineContext"
import { useOfflineSupported } from "@/app/_hooks/useOfflineSupported"
import { cn } from "@/lib/utils"

/**
 * The offline-mode switch.
 *
 * Lives in the nav's left column rather than the right-hand cluster because that
 * cluster is `hidden sm:flex` — theme, admin, storage and sign-out are all absent
 * on a phone, which is the one device this mode exists for. The left column is
 * empty below `sm` (the brand text is hidden), so this fills it.
 */
export function OfflineToggle({ className }: { className?: string }) {
  const supported = useOfflineSupported()
  const { offlineMode, setOfflineMode } = useOffline()

  // Nothing plays back offline without a service worker, so on an insecure origin
  // the switch would only ever produce an empty library. See docs/CONFIGURATION.md.
  if (!supported) return null

  const Icon = offlineMode ? SignalSlashIcon : SignalIcon

  return (
    <button
      type="button"
      onClick={() => setOfflineMode(!offlineMode)}
      aria-pressed={offlineMode}
      aria-label={offlineMode ? "Offline mode" : "Online mode"}
      title={offlineMode ? "Offline mode on — showing downloads only" : "Go offline"}
      className={cn(
        "items-center px-2 py-1 rounded transition-colors shrink-0",
        offlineMode
          ? "bg-status-warning/20 text-status-warning"
          : "text-text-muted hover:text-matrix hover:bg-matrix/10",
        // `flex` comes from the caller so a responsive `hidden sm:flex` isn't
        // fighting a hardcoded `flex` on the same element.
        className ?? "flex"
      )}
    >
      <Icon className="h-4 w-4" />
    </button>
  )
}
