"use client"

import { EllipsisHorizontalIcon } from "@heroicons/react/24/outline"

import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useOffline } from "@/app/context/OfflineContext"
import { useOfflineSupported } from "@/app/_hooks/useOfflineSupported"
import { cn } from "@/lib/utils"

type MobileNavMenuProps = {
  isAdmin: boolean
  adminMode: boolean
  setAdminMode: (enabled: boolean) => void
  onOpenStorage: () => void
  onOpenChangePassword: () => void
  /** Omitted while the MCP server is disabled, which hides the entry. */
  onOpenAccessTokens?: () => void
  onSignOut: () => void
}

/**
 * The phone's overflow menu.
 *
 * Exists because the nav's right-hand cluster is desktop-only, which left a phone
 * with no theme, admin, password or sign-out control at all — and because putting
 * the offline controls inline instead cost ~72px of a nav that already has to
 * scroll seven items across 390px.
 *
 * Theme is deliberately absent: ThemeSwitcher couples its trigger, an absolutely
 * positioned panel and its own document-level outside-click handler into one unit,
 * so inside a Radix menu the two outside-click systems fight and the panel is
 * clipped. Mobile has never had it; fixing that needs the trigger split from the
 * panel first.
 */
export function MobileNavMenu({
  isAdmin,
  adminMode,
  setAdminMode,
  onOpenStorage,
  onOpenChangePassword,
  onOpenAccessTokens,
  onSignOut,
}: MobileNavMenuProps) {
  const offlineSupported = useOfflineSupported()
  const { offlineMode, setOfflineMode } = useOffline()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title="More"
          aria-label="More options"
          className={cn(
            "flex items-center px-2 py-1 rounded shrink-0 transition-colors",
            // Offline mode is a mode, and this menu is the only place it shows on a
            // phone. Tinting the trigger keeps an active mode visible without
            // spending a second button's width on an inline indicator.
            offlineMode
              ? "text-status-warning bg-status-warning/20"
              : "text-text-muted hover:text-matrix hover:bg-matrix/10"
          )}
        >
          <EllipsisHorizontalIcon className="h-5 w-5" />
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end">
        {offlineSupported && (
          <>
            <DropdownMenuCheckboxItem
              checked={offlineMode}
              onCheckedChange={(checked) => setOfflineMode(checked === true)}
            >
              Offline mode
            </DropdownMenuCheckboxItem>
            <DropdownMenuItem onSelect={onOpenStorage}>Offline storage…</DropdownMenuItem>
            {!offlineMode && <DropdownMenuSeparator />}
          </>
        )}

        {/* Account entries are server actions. Signing out offline would discard
            the cached identity that unlocks the downloaded library, with no
            server to sign back in against, so they wait for the connection. */}
        {!offlineMode && (
          <>
            {isAdmin && (
              <DropdownMenuCheckboxItem
                checked={adminMode}
                onCheckedChange={(checked) => setAdminMode(checked === true)}
              >
                Admin view
              </DropdownMenuCheckboxItem>
            )}
            <DropdownMenuItem onSelect={onOpenChangePassword}>Change password…</DropdownMenuItem>
            {onOpenAccessTokens && (
              <DropdownMenuItem onSelect={onOpenAccessTokens}>MCP access tokens…</DropdownMenuItem>
            )}
            <DropdownMenuItem onSelect={onSignOut}>Sign out</DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
