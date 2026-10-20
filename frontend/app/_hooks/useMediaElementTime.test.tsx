// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, renderHook } from "@testing-library/react"

import { useMediaElementTime } from "./useMediaElementTime"

// RTL's automatic cleanup never registers with globals off — see .claude/rules/frontend.md.
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

/** An element whose currentTime advances on every read, as WebKit's does while playing. */
function playingWebKitAudio(start = 10) {
  const element = document.createElement("audio")
  let reads = 0
  Object.defineProperty(element, "currentTime", {
    configurable: true,
    get: () => start + reads++ * 0.001,
  })
  return element
}

describe("useMediaElementTime", () => {
  it("survives a currentTime that differs on every read", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {})
    const element = playingWebKitAudio()

    const { result } = renderHook(() => useMediaElementTime(element))

    expect(result.current).toBeCloseTo(10, 1)
    const before = result.current
    act(() => {
      element.dispatchEvent(new Event("timeupdate"))
    })
    expect(result.current).toBeGreaterThan(before)
    expect(consoleError).not.toHaveBeenCalled()
  })

  it("follows the element's timeupdate, seeked and emptied events", () => {
    const element = document.createElement("audio")
    const { result } = renderHook(() => useMediaElementTime(element))
    expect(result.current).toBe(0)

    element.currentTime = 42
    act(() => {
      element.dispatchEvent(new Event("seeked"))
    })
    expect(result.current).toBe(42)

    element.currentTime = 43.5
    act(() => {
      element.dispatchEvent(new Event("timeupdate"))
    })
    expect(result.current).toBe(43.5)

    element.currentTime = 0
    act(() => {
      element.dispatchEvent(new Event("emptied"))
    })
    expect(result.current).toBe(0)
  })

  it("reads 0 with no element", () => {
    const { result } = renderHook(() => useMediaElementTime(null))
    expect(result.current).toBe(0)
  })
})
