// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"

import type { Download } from "@/app/types/DownloadsOptions"

const put = vi.fn()
const del = vi.fn()

vi.mock("axios", () => {
  const axios = {
    put: (...args: unknown[]) => put(...args),
    delete: (...args: unknown[]) => del(...args),
    defaults: { withCredentials: false },
    interceptors: { response: { use: () => {} } },
    isCancel: () => false,
    isAxiosError: () => false,
  }
  return { default: axios, isCancel: axios.isCancel, isAxiosError: axios.isAxiosError }
})
vi.mock("react-hot-toast", () => ({ default: { success: vi.fn(), error: vi.fn() } }))
vi.mock("@/app/context/AuthContext", () => ({ useAuth: () => ({ user: { id: 1 } }) }))
vi.mock("@/app/_components/TagsDialog", () => ({ TagsDialog: () => null }))
vi.mock("@/app/_components/ShareDialog", () => ({ ShareDialog: () => null }))
vi.mock("@/app/_components/AddToPlaylistDialog", () => ({ AddToPlaylistDialog: () => null }))

const { MediaBulkActions } = await import("./MediaBulkActions")

const row = (id: number, media_type: string, status: string): Download =>
  ({ media_details_id: id, media_type, status, owner_id: 1, title: `item ${id}` }) as Download

const LIBRARY = row(1, "VIDEO", "COMPLETE")
const TRANSCRIPT_ONLY = row(2, "TRANSCRIPT", "COMPLETE")
const DELETED = row(3, "VIDEO", "DELETED")

const renderBulk = (selectedItems: Download[], status?: string) =>
  render(
    <MediaBulkActions
      selectedItems={selectedItems}
      status={status}
      onClearSelection={vi.fn()}
      onRefresh={vi.fn()}
    />,
  )

// RTL's automatic cleanup never registers with globals off — see .claude/rules/frontend.md.
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("MediaBulkActions in the Transcripts view", () => {
  it("deletes each row the way its own button does", async () => {
    del.mockResolvedValue({ data: { deleted: 2 } })
    renderBulk([LIBRARY, TRANSCRIPT_ONLY, DELETED], "TRANSCRIPTS")

    fireEvent.click(screen.getByRole("button", { name: "Delete (3)" }))
    fireEvent.click(screen.getByRole("button", { name: "Delete 3 Items" }))

    await waitFor(() => expect(del).toHaveBeenCalledTimes(2))
    expect(del).toHaveBeenCalledWith(expect.stringMatching(/\/media-details\/bulk-delete$/), {
      data: { media_details_ids: [1, 2], keep_transcripts: true },
    })
    expect(del).toHaveBeenCalledWith(expect.stringMatching(/\/media-details\/3\/transcripts$/))
  })

  it("leaves out Add to Playlist", () => {
    renderBulk([LIBRARY, TRANSCRIPT_ONLY], "TRANSCRIPTS")

    expect(screen.getByRole("button", { name: "Tags" })).toBeDefined()
    expect(screen.queryByRole("button", { name: "Add to Playlist" })).toBeNull()
  })

  it("offers neither tags nor a transcript to keep for deleted rows alone", () => {
    renderBulk([DELETED], "TRANSCRIPTS")

    expect(screen.getByRole("button", { name: "Tags" }).hasAttribute("disabled")).toBe(true)
    expect(screen.getByRole("button", { name: "Share" }).hasAttribute("disabled")).toBe(true)

    fireEvent.click(screen.getByRole("button", { name: "Delete (1)" }))
    expect(screen.getByText(/only the kept transcript is removed/)).toBeDefined()
    expect(screen.queryByLabelText(/Keep transcripts/)).toBeNull()
  })

  it("counts only the rows tags apply to", () => {
    renderBulk([LIBRARY, DELETED], "TRANSCRIPTS")

    expect(screen.getByRole("button", { name: /^Tags/ }).textContent).toBe("Tags(1 of 2)")
  })
})

describe("MediaBulkActions in the Library", () => {
  it("keeps every action and the keep-transcripts choice", () => {
    renderBulk([LIBRARY])

    expect(screen.getByRole("button", { name: "Add to Playlist" })).toBeDefined()
    fireEvent.click(screen.getByRole("button", { name: "Delete (1)" }))
    expect(screen.getByLabelText(/Keep transcripts/)).toBeDefined()
  })
})
