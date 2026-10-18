"use client"

import { useView } from "@/app/context/ViewContext"
import { useFetchEffect } from "@/app/_hooks/useFetchEffect"
import { cn } from "@/lib/utils"
import { motion } from "framer-motion"
import {
  ArrowDownTrayIcon,
  ListBulletIcon,
  ScissorsIcon,
  PlayCircleIcon,
  QueueListIcon,
  ChartBarIcon,
  Cog6ToothIcon,
  EyeIcon,
  UserCircleIcon,
} from "@heroicons/react/24/outline"
import { useEffect, useState, useCallback } from "react"
import { apiUrl } from "@/app/lib/api"
import { useAuth } from "@/app/context/AuthContext"
import { useAdmin } from "@/app/context/AdminContext"
import { ThemeSwitcher } from "@/app/_components/ThemeSwitcher"
import { OfflineToggle } from "@/app/_components/OfflineToggle"
import { useOffline } from "@/app/context/OfflineContext"
import {
  OfflineStorageButton,
  OfflineStorageDialog,
} from "@/app/_components/OfflineStorageDialog"
import { MobileNavMenu } from "@/app/_components/MobileNavMenu"
import { ThemePicker } from "@/app/_components/ThemePicker"
import { ChangePasswordDialog } from "@/app/_components/auth/ChangePasswordDialog"
import { AccessTokensDialog } from "@/app/_components/auth/AccessTokensDialog"
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu"
import { useDocumentTheme } from "@/app/_hooks/useDocumentTheme"
import { formatBytes } from "@/app/utils"

type NavItemProps = {
  label: string
  viewKey: "downloads" | "subscriptions" | "clips" | "playlists" | "tasks" | "stats" | "settings"
  icon: React.ReactNode
  isActive: boolean
  onClick: () => void
}

function NavItem({ label, icon, isActive, onClick }: NavItemProps) {
  return (
    <li className="relative">
      <button
        onClick={onClick}
        className={cn(
          "flex items-center gap-2 px-4 sm:px-3 lg:px-4 py-2 font-mono text-sm transition-all duration-200 rounded-md",
          "hover:text-matrix hover:bg-matrix/5",
          isActive
            ? "text-matrix text-glow-sm"
            : "text-text-secondary"
        )}
      >
        <span className="w-5 h-5">{icon}</span>
        <span className="hidden lg:inline">{label}</span>
      </button>
      {isActive && (
        <motion.div
          layoutId="nav-indicator"
          className="absolute -bottom-1 left-2 right-2 h-0.5 bg-matrix rounded-full shadow-glow-sm"
          initial={false}
          transition={{ type: "spring", stiffness: 500, damping: 30 }}
        />
      )}
    </li>
  )
}

export function NavigationBar() {
  const { view, setView } = useView()
  const { user, logout } = useAuth()
  const { adminMode, setAdminMode } = useAdmin()
  const { offlineMode } = useOffline()
  const [appVersion, setAppVersion] = useState<string | null>(null)
  const [ytdlpVersion, setYtdlpVersion] = useState<string | null>(null)
  const [storageUsed, setStorageUsed] = useState<number | null>(null)
  const [storageLimit, setStorageLimit] = useState<number | null>(null)
  const [changePasswordOpen, setChangePasswordOpen] = useState(false)
  const [accessTokensOpen, setAccessTokensOpen] = useState(false)
  // Held here, not inside the dialog: the mobile entry point is a menu item, and
  // the menu unmounts its own children on select.
  const [storageOpen, setStorageOpen] = useState(false)

  // Track theme changes for conditional rendering (e.g. GeoCities color picker)
  const currentTheme = useDocumentTheme()

  useEffect(() => {
    const fetchVersionInfo = async () => {
      try {
        const response = await fetch(apiUrl('/ytdl/version'))
        const data = await response.json()
        setAppVersion(data.app_version)
        setYtdlpVersion(data.ytdlp_version)
      } catch (error) {
        console.error('Failed to fetch version info:', error)
      }
    }

    fetchVersionInfo()
  }, [])

  const fetchStorage = useCallback(async () => {
    if (!user) return
    try {
      const response = await fetch(apiUrl('/auth/me/storage'), { credentials: 'include' })
      if (response.ok) {
        const data = await response.json()
        setStorageUsed(data.storage_used_bytes)
        setStorageLimit(data.storage_limit_bytes)
      }
    } catch {
      // Silently fail — indicator just won't show
    }
  }, [user])

  const { refetch: refreshStorage } = useFetchEffect(fetchStorage, [fetchStorage], {
    enabled: !offlineMode,
    pollMs: offlineMode ? null : 60_000,
  })

  useEffect(() => {
    window.addEventListener('task-completed', refreshStorage)
    return () => window.removeEventListener('task-completed', refreshStorage)
  }, [refreshStorage])

  const storagePercent = storageLimit && storageUsed !== null
    ? Math.min(100, (storageUsed / storageLimit) * 100)
    : 0
  const storageWarning = storagePercent > 80
  const storageCritical = storagePercent > 95

  const allNavItems = [
    {
      label: "Downloads",
      viewKey: "downloads" as const,
      icon: <ArrowDownTrayIcon />,
    },
    {
      label: "Subscriptions",
      viewKey: "subscriptions" as const,
      icon: <ListBulletIcon />,
    },
    {
      label: "Clips",
      viewKey: "clips" as const,
      icon: <ScissorsIcon />,
    },
    {
      label: "Playlists",
      viewKey: "playlists" as const,
      icon: <PlayCircleIcon />,
    },
    {
      label: "Tasks",
      viewKey: "tasks" as const,
      icon: <QueueListIcon />,
    },
    {
      label: "Stats",
      viewKey: "stats" as const,
      icon: <ChartBarIcon />,
    },
    {
      label: "Settings",
      viewKey: "settings" as const,
      icon: <Cog6ToothIcon />,
      adminOnly: true,
    },
  ]

  const navItems = allNavItems.filter(
    (item) => !("adminOnly" in item && item.adminOnly) || adminMode
  )

  return (
    <nav className="sticky top-0 z-40 w-full border-b border-border bg-bg-terminal/95 backdrop-blur-sm supports-backdrop-filter:bg-bg-terminal/60">
      {/* Standalone iOS draws the page under the status bar (viewportFit cover +
          black-translucent, layout.tsx); light themes paint this dark in globals.css
          so the white clock stays legible. */}
      <div className="status-bar-inset h-[env(safe-area-inset-top)]" />
      <div className="mx-auto px-4">
        <div className="grid grid-cols-[1fr_auto_1fr] h-14 items-center">
          {/* Logo/Brand — everything here is hidden below sm. Keep it that way:
              min-w-0 lets this grid track shrink below its content, and the wrapper
              overflows visibly, so always-visible children spill over the nav. */}
          <div className="flex items-center gap-2 min-w-0">
            <span className="hidden sm:inline text-sm font-mono font-semibold text-matrix">
              ytdl-hoarder
            </span>
            {appVersion && (
              <span className="hidden md:inline-block text-xs text-text-muted font-mono">
                v{appVersion}
              </span>
            )}
            {ytdlpVersion && (
              <span className="hidden lg:inline-block text-xs text-text-muted/60 font-mono">
                (yt-dlp {ytdlpVersion})
              </span>
            )}
          </div>

          {/* Navigation - Centered */}
          <ul className="flex items-center gap-1 sm:gap-2 min-w-0 max-w-full overflow-x-auto pb-1 -mb-1 scrollbar-none [&::-webkit-scrollbar]:hidden">
            {navItems.map((item) => (
              <NavItem
                key={item.viewKey}
                label={item.label}
                viewKey={item.viewKey}
                icon={item.icon}
                isActive={view === item.viewKey}
                onClick={() => setView(item.viewKey)}
              />
            ))}
          </ul>

          {/* Right side - User info + Theme. The cluster itself renders at every
              width; each desktop-only child carries its own `hidden sm:*`, so a
              phone gets exactly one item here — the overflow menu. */}
          <div className="flex items-center gap-3 justify-end">
            <OfflineToggle className="hidden sm:flex" />
            <OfflineStorageButton
              onClick={() => setStorageOpen(true)}
              className="hidden sm:flex"
            />
            <span className="hidden sm:contents">
              <ThemeSwitcher />
              {currentTheme === "geocities" && !offlineMode && <ThemePicker />}
            </span>
            {user && (
              <>
                {user.is_admin && !offlineMode && (
                  <button
                    onClick={() => setAdminMode(!adminMode)}
                    className={cn(
                      "hidden sm:flex items-center gap-1 px-2 py-1 text-xs font-mono rounded transition-colors",
                      adminMode
                        ? "bg-status-warning/20 text-status-warning border border-status-warning/30"
                        : "text-text-muted hover:text-text-secondary"
                    )}
                    title={adminMode ? "Admin view: seeing all users' data" : "Toggle admin view"}
                  >
                    <EyeIcon className="w-3.5 h-3.5" />
                    {adminMode && <span>Admin</span>}
                  </button>
                )}
                {storageLimit !== null && storageUsed !== null && (
                  <span
                    className={cn(
                      "hidden lg:inline text-xs font-mono px-2 py-0.5 rounded",
                      storageCritical
                        ? "text-status-error bg-status-error/10"
                        : storageWarning
                        ? "text-status-warning bg-status-warning/10"
                        : "text-text-muted"
                    )}
                    title={`Storage: ${storageUsed.toLocaleString()} / ${storageLimit.toLocaleString()} bytes`}
                  >
                    {formatBytes(storageUsed)} / {formatBytes(storageLimit)}
                  </span>
                )}
                {/* Signing out offline would discard the cached identity that
                    unlocks the downloaded library, with no server to sign back
                    in against — so the account menu waits for the connection. */}
                {offlineMode ? (
                  <span
                    className="hidden sm:flex items-center gap-1 px-2 py-1 text-xs font-mono text-text-muted shrink-0"
                    title={user.username}
                  >
                    <UserCircleIcon className="w-4 h-4 lg:hidden" />
                    <span className="hidden lg:inline">{user.username}</span>
                  </span>
                ) : (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        className="hidden sm:flex items-center gap-1 px-2 py-1 text-xs font-mono text-text-muted hover:text-matrix transition-colors rounded shrink-0"
                        title={user.username}
                      >
                        <UserCircleIcon className="w-4 h-4 lg:hidden" />
                        <span className="hidden lg:inline">{user.username}</span>
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => setChangePasswordOpen(true)}>
                        Change Password
                      </DropdownMenuItem>
                      {user.mcp_enabled && (
                        <DropdownMenuItem onClick={() => setAccessTokensOpen(true)}>
                          MCP Access Tokens
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuItem onClick={logout}>
                        Sign out
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}

                <span className="sm:hidden">
                  <MobileNavMenu
                    isAdmin={Boolean(user.is_admin)}
                    adminMode={adminMode}
                    setAdminMode={setAdminMode}
                    onOpenStorage={() => setStorageOpen(true)}
                    onOpenChangePassword={() => setChangePasswordOpen(true)}
                    onOpenAccessTokens={
                      user.mcp_enabled ? () => setAccessTokensOpen(true) : undefined
                    }
                    onSignOut={logout}
                  />
                </span>
              </>
            )}
          </div>
        </div>
      </div>

      <ChangePasswordDialog open={changePasswordOpen} onOpenChange={setChangePasswordOpen} />
      {user?.mcp_enabled && (
        <AccessTokensDialog open={accessTokensOpen} onOpenChange={setAccessTokensOpen} />
      )}
      <OfflineStorageDialog open={storageOpen} onOpenChange={setStorageOpen} />
    </nav>
  )
}
