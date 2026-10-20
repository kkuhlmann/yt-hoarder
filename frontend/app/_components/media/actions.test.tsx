// @vitest-environment jsdom
import "fake-indexeddb/auto"

import { describe, expect, it, vi } from "vitest"

import { buildMediaActions, type BuildMediaActionsArgs } from "./actions"
import type { MediaActions } from "@/app/_hooks/useMediaActions"
import type { MediaDialogs } from "./MediaActionDialogs"

// Never invoked: the tests read descriptor keys, not behaviour.
const args: BuildMediaActionsArgs = {
  status: "COMPLETE",
  user: { id: 1, is_admin: false },
  actions: {} as MediaActions,
  dialogs: { open: vi.fn() } as unknown as MediaDialogs,
  onClip: vi.fn(),
}

const keys = (overrides: Partial<BuildMediaActionsArgs>) =>
  buildMediaActions({ ...args, ...overrides }).map((action) => action.key)

describe("buildMediaActions", () => {
  it("offers the full set online", () => {
    expect(keys({})).toEqual([
      "transcript",
      "tags",
      "playlist",
      "share",
      "offline",
      "clip",
      "delete",
    ])
  })

  it("offers the Transcripts view only what applies to a file-less row", () => {
    expect(keys({ status: "TRANSCRIPTS", onViewTranscript: vi.fn() })).toEqual([
      "viewTranscript",
      "source",
      "tags",
      "share",
      "delete",
    ])
  })

  it("leaves View transcript out rather than render a dead button", () => {
    expect(keys({ status: "TRANSCRIPTS" })).not.toContain("viewTranscript")
  })

  it("deletes a deleted row's transcript rather than the row", () => {
    const open = vi.fn()
    const actions = buildMediaActions({
      ...args,
      status: "TRANSCRIPTS",
      dialogs: { open } as unknown as MediaDialogs,
    })
    const remove = actions.find((a) => a.key === "delete")!
    const row = { media_details_id: 1, status: "DELETED" } as never

    remove.onClick!(row)

    expect(open).toHaveBeenCalledWith("deleteTranscripts", row)
  })

  it("keeps only the offline button in offline mode, whatever the scope", () => {
    // Playback-only: every other action writes to the server. The DELETED and
    // SKIPPED sets matter too — a scope chosen before the switch must not leave
    // "Permanently delete" one click away on a downloaded row.
    expect(keys({ offlineMode: true })).toEqual(["offline"])
    expect(keys({ offlineMode: true, status: "DELETED" })).toEqual(["offline"])
    expect(keys({ offlineMode: true, status: "SKIPPED" })).toEqual(["offline"])
  })
})
