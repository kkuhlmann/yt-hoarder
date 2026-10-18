// @vitest-environment jsdom
import "fake-indexeddb/auto"

import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

const mockSupported = vi.fn(() => true)
const mockOffline = vi.fn(() => ({ offlineMode: false, setOfflineMode: vi.fn() }))

vi.mock("@/app/_hooks/useOfflineSupported", () => ({
  useOfflineSupported: () => mockSupported(),
}))
vi.mock("@/app/context/OfflineContext", () => ({
  useOffline: () => mockOffline(),
}))

const { MobileNavMenu } = await import("./MobileNavMenu")
const { OfflineStorageDialog } = await import("./OfflineStorageDialog")

// RTL's automatic cleanup never registers with globals off — see .claude/rules/frontend.md.
afterEach(() => {
  cleanup()
  mockSupported.mockReturnValue(true)
  mockOffline.mockReturnValue({ offlineMode: false, setOfflineMode: vi.fn() })
})

function renderMenu(props: Partial<Parameters<typeof MobileNavMenu>[0]> = {}) {
  return render(
    <MobileNavMenu
      isAdmin={false}
      adminMode={false}
      setAdminMode={vi.fn()}
      onOpenStorage={vi.fn()}
      onOpenChangePassword={vi.fn()}
      onSignOut={vi.fn()}
      {...props}
    />
  )
}

const openMenu = () => {
  const trigger = screen.getByRole("button", { name: "More options" })
  // Radix opens on pointerdown, not click.
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false, pointerType: "mouse" })
  return trigger
}

describe("MobileNavMenu", () => {
  it("renders a single trigger button", () => {
    renderMenu()
    expect(screen.getByRole("button", { name: "More options" })).toBeDefined()
  })

  it("tints the trigger while offline mode is active", () => {
    // The only place an active offline mode is visible on a phone, now that the
    // inline indicator is gone.
    mockOffline.mockReturnValue({ offlineMode: true, setOfflineMode: vi.fn() })
    renderMenu()

    const trigger = screen.getByRole("button", { name: "More options" })
    expect(trigger.className).toContain("text-status-warning")
  })

  it("leaves the trigger untinted while online", () => {
    renderMenu()
    const trigger = screen.getByRole("button", { name: "More options" })
    expect(trigger.className).not.toContain("text-status-warning")
  })

  it("offers the offline entries when offline support is available", () => {
    renderMenu()
    openMenu()

    expect(screen.getByText("Offline mode")).toBeDefined()
    expect(screen.getByText("Offline storage…")).toBeDefined()
  })

  it("omits the offline entries on an origin that cannot support them", () => {
    // An insecure or dev origin registers no service worker, so these would be
    // dead entries.
    mockSupported.mockReturnValue(false)
    renderMenu()
    openMenu()

    expect(screen.queryByText("Offline mode")).toBeNull()
    expect(screen.queryByText("Offline storage…")).toBeNull()
    // The account entries are unrelated to offline support and must survive.
    expect(screen.getByText("Sign out")).toBeDefined()
  })

  it("keeps only the offline entries while offline mode is on", () => {
    // Signing out offline would discard the cached identity that unlocks the
    // downloaded library; the other two are server actions as well.
    mockOffline.mockReturnValue({ offlineMode: true, setOfflineMode: vi.fn() })
    renderMenu({ isAdmin: true })
    openMenu()

    expect(screen.getByText("Offline mode")).toBeDefined()
    expect(screen.getByText("Offline storage…")).toBeDefined()
    expect(screen.queryByText("Sign out")).toBeNull()
    expect(screen.queryByText("Change password…")).toBeNull()
    expect(screen.queryByText("Admin view")).toBeNull()
  })

  it("shows the admin toggle only to an admin", () => {
    renderMenu({ isAdmin: false })
    openMenu()
    expect(screen.queryByText("Admin view")).toBeNull()

    cleanup()
    renderMenu({ isAdmin: true })
    openMenu()
    expect(screen.getByText("Admin view")).toBeDefined()
  })

  it("offers MCP access tokens only when its owner passes a handler", () => {
    // NavigationBar omits the handler while the MCP server is disabled.
    renderMenu()
    openMenu()
    expect(screen.queryByText("MCP access tokens…")).toBeNull()

    cleanup()
    const onOpenAccessTokens = vi.fn()
    renderMenu({ onOpenAccessTokens })
    openMenu()
    fireEvent.click(screen.getByText("MCP access tokens…"))
    expect(onOpenAccessTokens).toHaveBeenCalledOnce()
  })

  it("hides MCP access tokens while offline", () => {
    mockOffline.mockReturnValue({ offlineMode: true, setOfflineMode: vi.fn() })
    renderMenu({ onOpenAccessTokens: vi.fn() })
    openMenu()

    expect(screen.queryByText("MCP access tokens…")).toBeNull()
  })

  it("asks its owner to open the storage dialog rather than opening one itself", () => {
    // The dialog is owned by NavigationBar precisely because this menu unmounts
    // its own children on select.
    const onOpenStorage = vi.fn()
    renderMenu({ onOpenStorage })
    openMenu()

    fireEvent.click(screen.getByText("Offline storage…"))
    expect(onOpenStorage).toHaveBeenCalledOnce()
  })
})

describe("OfflineStorageDialog is controlled", () => {
  it("renders nothing when closed and its content when open", () => {
    const { rerender } = render(
      <OfflineStorageDialog open={false} onOpenChange={vi.fn()} />
    )
    expect(screen.queryByText("Offline storage")).toBeNull()

    rerender(<OfflineStorageDialog open onOpenChange={vi.fn()} />)
    expect(screen.getByText("Offline storage")).toBeDefined()
  })
})
