// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, renderHook } from "@testing-library/react"

// The hook fetches folders on mount; these tests only exercise its path derivation.
// The mock also has to satisfy app/lib/api.ts, which configures axios at import.
vi.mock("axios", () => {
  const axios = {
    get: vi.fn(() => new Promise(() => {})),
    defaults: { withCredentials: false },
    interceptors: { response: { use: () => {} } },
    isCancel: () => false,
  }
  return { default: axios, isCancel: axios.isCancel }
})
vi.mock("@/app/context/AdminContext", () => ({ useAdmin: () => ({ adminParam: {} }) }))

const { useDownloadGrouping } = await import("./useDownloadGrouping")

afterEach(cleanup)

const args = { enabled: true, status: "COMPLETE", search: "", tagIds: [], minRating: null }

function open(result: { current: ReturnType<typeof useDownloadGrouping> }, key: string) {
  act(() => result.current.openFolder({ key, label: key } as never))
}

describe("useDownloadGrouping scope", () => {
  it("has no scope until a folder is opened", () => {
    const { result } = renderHook(() => useDownloadGrouping(args))

    act(() => result.current.setGroupDim("released"))

    // A dimension picked but no folder opened selects nothing.
    expect(result.current.scope).toBeNull()
    expect(result.current.scopeKey).toBeNull()
    expect(result.current.leaf).toBeNull()
  })

  it("scopes to a whole year at depth 1, where there is still no leaf", () => {
    const { result } = renderHook(() => useDownloadGrouping(args))

    act(() => result.current.setGroupDim("released"))
    open(result, "2024")

    expect(result.current.scope?.filter).toEqual({
      dateField: "released",
      year: 2024,
      month: undefined,
    })
    expect(result.current.atLeaf).toBe(false)
    // The media list must not treat a half-open date path as a leaf.
    expect(result.current.leaf).toBeNull()
    expect(result.current.leafKey).toBeNull()
  })

  it("narrows to the month at depth 2, where scope and leaf converge", () => {
    const { result } = renderHook(() => useDownloadGrouping(args))

    act(() => result.current.setGroupDim("released"))
    open(result, "2024")
    open(result, "2024-03")

    expect(result.current.scope?.filter).toEqual({
      dateField: "released",
      year: 2024,
      month: 3,
    })
    expect(result.current.atLeaf).toBe(true)
    expect(result.current.leaf).toEqual(result.current.scope)
    expect(result.current.leafKey).toBe("released:2024/2024-03")
  })

  it("reaches its leaf in one step for single-level dimensions", () => {
    const { result } = renderHook(() => useDownloadGrouping(args))

    act(() => result.current.setGroupDim("channel"))
    open(result, "NASA")

    expect(result.current.scope?.filter).toEqual({ channel: "NASA" })
    expect(result.current.leaf).toEqual(result.current.scope)
  })

  it("turns the untagged bucket into a filter rather than a tag id", () => {
    const { result } = renderHook(() => useDownloadGrouping(args))

    act(() => result.current.setGroupDim("tag"))
    open(result, "untagged")

    expect(result.current.scope).toEqual({ tagIds: [], filter: { untagged: true } })
  })

  it("drops back to the parent scope on goUp", () => {
    const { result } = renderHook(() => useDownloadGrouping(args))

    act(() => result.current.setGroupDim("downloaded"))
    open(result, "2025")
    open(result, "2025-07")
    act(() => result.current.goUp())

    expect(result.current.scope?.filter).toEqual({
      dateField: "downloaded",
      year: 2025,
      month: undefined,
    })
    expect(result.current.leaf).toBeNull()
  })
})

describe("useDownloadGrouping in the Transcripts view", () => {
  const transcripts = { ...args, status: "TRANSCRIPTS" }

  it("asks for the Transcripts view's folders, not a status's", async () => {
    const axios = (await import("axios")).default as unknown as {
      get: ReturnType<typeof vi.fn>
    }
    axios.get.mockClear()
    const { result } = renderHook(() => useDownloadGrouping(transcripts))

    act(() => result.current.setGroupDim("channel"))

    const [, config] = axios.get.mock.calls.at(-1) as [string, { params: Record<string, unknown> }]
    expect(config.params).toMatchObject({ group_by: "channel", has_transcript: true })
    expect(config.params).not.toHaveProperty("status")
  })

  it("calls the download date the added date", () => {
    const { result } = renderHook(() => useDownloadGrouping(transcripts))

    act(() => result.current.setGroupDim("downloaded"))

    expect(result.current.breadcrumb.dimLabel).toBe("Added date")
  })
})
