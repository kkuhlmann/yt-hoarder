// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, renderHook } from "@testing-library/react"
import { useViewMode } from "./useViewMode"

// Node's own experimental localStorage global shadows jsdom's and is unusable without
// --localstorage-file, so each test gets a plain in-memory one.
beforeEach(() => {
  const items = new Map<string, string>()
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => items.set(key, value),
  })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe("useViewMode", () => {
  it("starts the Transcripts view on the Library's choice", () => {
    localStorage.setItem("viewMode:downloads", "table")

    const { result } = renderHook(() => useViewMode("transcripts"))

    expect(result.current[0]).toBe("table")
  })

  it("keeps a Transcripts choice to itself once one is made", () => {
    localStorage.setItem("viewMode:downloads", "table")
    const transcripts = renderHook(() => useViewMode("transcripts"))
    const library = renderHook(() => useViewMode("downloads"))

    act(() => transcripts.result.current[1]("grid"))
    act(() => library.result.current[1]("table"))

    expect(transcripts.result.current[0]).toBe("grid")
    expect(library.result.current[0]).toBe("table")
  })
})
