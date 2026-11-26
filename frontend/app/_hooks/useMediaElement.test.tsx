// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, renderHook } from "@testing-library/react"

import { apiUrl } from "@/app/lib/api"
import { mediaApi } from "@/app/lib/mediaApi"
import type { UseMediaElementOptions } from "./useMediaElement"

const savePlaybackPosition = vi.fn()
const notePlaybackPosition = () => {}

vi.mock("@/app/lib/playbackSync", () => ({
  savePlaybackPosition: (...args: unknown[]) => savePlaybackPosition(...args),
}))
vi.mock("@/app/context/MediaPlayerContext", () => ({
  useMediaPlayer: () => ({ notePlaybackPosition }),
}))

const { useMediaElement } = await import("./useMediaElement")

// RTL's automatic cleanup never registers with globals off — see .claude/rules/frontend.md.
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  savePlaybackPosition.mockClear()
})

const stream = (id: number) => apiUrl(mediaApi.stream(id))

const NEXT = { id: 2, startTime: 60, duration: 600 }

function mount(props: UseMediaElementOptions) {
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined)
  const setSrc = vi.spyOn(HTMLMediaElement.prototype, "src", "set")
  const element = document.createElement("audio")
  const ref = { current: element }
  const hook = renderHook((p: UseMediaElementOptions) => useMediaElement(ref, p), {
    initialProps: props,
  })
  play.mockClear()
  setSrc.mockClear()
  return { element, play, setSrc, ...hook }
}

const end = (element: HTMLMediaElement) =>
  act(() => {
    element.dispatchEvent(new Event("ended"))
  })

const tick = (element: HTMLMediaElement, at: number) => {
  element.currentTime = at
  act(() => {
    element.dispatchEvent(new Event("timeupdate"))
  })
}

describe("useMediaElement auto-advance", () => {
  it("starts the next track before the ended event returns", () => {
    const onEnded = vi.fn()
    const { element, play } = mount({ id: 1, initialDuration: 300, onEnded, autoAdvance: NEXT })
    expect(element.getAttribute("src")).toBe(stream(1))

    act(() => {
      element.dispatchEvent(new Event("ended"))
      // Still inside act, so nothing React scheduled has run yet. On a locked
      // iPhone this is all that happens before the page is suspended.
      expect(element.getAttribute("src")).toBe(stream(2))
      expect(element.currentTime).toBe(60)
      expect(play).toHaveBeenCalledTimes(1)
      expect(onEnded).toHaveBeenCalledTimes(1)
    })
  })

  it("lets the render that catches up neither reload nor seek the track", () => {
    const { element, setSrc, rerender } = mount({ id: 1, initialDuration: 300, autoAdvance: NEXT })
    end(element)
    setSrc.mockClear()
    element.currentTime = 200 // played on while the render was deferred

    rerender({ id: 2, startTime: 60, initialDuration: 600 })

    expect(setSrc).not.toHaveBeenCalled()
    expect(element.getAttribute("src")).toBe(stream(2))
    expect(element.currentTime).toBe(200)
  })

  it("never saves the next track's position under the finished track's id", () => {
    const { element, rerender } = mount({ id: 1, initialDuration: 300, autoAdvance: NEXT })
    tick(element, 298)
    expect(savePlaybackPosition).toHaveBeenLastCalledWith(1, 298)
    savePlaybackPosition.mockClear()

    end(element)
    tick(element, 70)
    expect(savePlaybackPosition).not.toHaveBeenCalled()

    rerender({ id: 2, startTime: 60, initialDuration: 600 })
    expect(savePlaybackPosition).toHaveBeenLastCalledWith(2, 70)
  })

  it("starts a next track resumed near its end from the beginning", () => {
    const { element, rerender } = mount({
      id: 1,
      initialDuration: 300,
      autoAdvance: { id: 2, startTime: 590, duration: 600 },
    })
    end(element)
    expect(element.currentTime).toBe(0)

    element.currentTime = 15
    rerender({ id: 2, startTime: 590, initialDuration: 600 })
    expect(element.currentTime).toBe(15)
  })

  it("loads and tracks a later track change as usual", () => {
    const { element, rerender } = mount({ id: 1, initialDuration: 300, autoAdvance: NEXT })
    end(element)
    rerender({ id: 2, startTime: 60, initialDuration: 600 })

    rerender({ id: 3, startTime: 0, initialDuration: 400 })
    expect(element.getAttribute("src")).toBe(stream(3))
    expect(element.currentTime).toBe(0)

    tick(element, 30)
    expect(savePlaybackPosition).toHaveBeenLastCalledWith(3, 30)
  })

  it("leaves the element to onEnded's render without a next track", () => {
    const onEnded = vi.fn()
    const { element, play, setSrc } = mount({ id: 1, onEnded })

    end(element)

    expect(setSrc).not.toHaveBeenCalled()
    expect(play).not.toHaveBeenCalled()
    expect(onEnded).toHaveBeenCalledTimes(1)
  })
})
