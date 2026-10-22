// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, renderHook } from "@testing-library/react"

const post = vi.fn()

vi.mock("axios", () => {
  const axios = {
    post: (...args: unknown[]) => post(...args),
    defaults: { withCredentials: false },
    interceptors: { response: { use: () => {} } },
    isCancel: () => false,
    isAxiosError: () => false,
  }
  return { default: axios, isCancel: axios.isCancel, isAxiosError: axios.isAxiosError }
})
vi.mock("react-hot-toast", () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const { MIN_CLIP_SECONDS, useClipEditor } = await import("./useClipEditor")

afterEach(() => {
  cleanup()
  post.mockReset()
})

function setup(duration: number, initialEnd = Math.min(duration, 30)) {
  return renderHook(
    ({ duration }: { duration: number }) =>
      useClipEditor({
        mediaDetailsId: 1,
        duration,
        currentTime: 0,
        mediaRef: { current: null },
        onSeek: () => {},
        onSaved: () => {},
        initialEnd,
      }),
    { initialProps: { duration } }
  )
}

describe("useClipEditor", () => {
  it("keeps end ahead of start when the duration is unknown", () => {
    // The transcript-search case: a row with no duration, entered mid-video, so
    // the end clamps against 0 while the start comes from the playhead.
    const { result } = setup(0)

    act(() => result.current.setStartTime(295))

    expect(result.current.endTime).toBeGreaterThan(result.current.startTime)
    expect(result.current.clipDuration).toBeGreaterThanOrEqual(MIN_CLIP_SECONDS)
  })

  it("never reports a negative clip length", () => {
    const { result } = setup(0)

    act(() => result.current.setEndTime(0))
    act(() => result.current.setStartTime(600))

    expect(result.current.clipDuration).toBeGreaterThanOrEqual(0)
  })

  it("seeds a usable end once the real duration lands", () => {
    const { result, rerender } = setup(0)

    expect(result.current.endTime).toBe(MIN_CLIP_SECONDS)

    rerender({ duration: 600 })

    expect(result.current.startTime).toBe(0)
    expect(result.current.endTime).toBe(30)
  })

  it("clamps the end to the duration", () => {
    const { result } = setup(600)

    act(() => result.current.setEndTime(999))

    expect(result.current.endTime).toBe(600)
  })

  it("treats a non-finite duration as unknown rather than clamping to NaN", () => {
    const { result } = setup(Number.POSITIVE_INFINITY, 30)

    expect(Number.isFinite(result.current.endTime)).toBe(true)
    expect(result.current.endTime).toBeGreaterThan(result.current.startTime)
  })
})

describe("useClipEditor saving", () => {
  async function save(outputType?: "AUDIO" | "VIDEO") {
    post.mockResolvedValue({ status: 201 })
    const { result } = renderHook(() =>
      useClipEditor({
        mediaDetailsId: 7,
        duration: 600,
        currentTime: 0,
        onSeek: () => {},
        onSaved: () => {},
        outputType,
      })
    )
    act(() => result.current.setTitle("Highlight"))
    await act(() => result.current.handleSaveClip())
    return post.mock.calls[0][1]
  }

  it("sends the chosen type for an item clipped from its source", async () => {
    expect(await save("AUDIO")).toMatchObject({ media_details_id: 7, media_type: "AUDIO" })
  })

  it("sends no type for an item clipped as what it is", async () => {
    expect(await save()).not.toHaveProperty("media_type")
  })
})

describe("useClipEditor loop preview without a media element", () => {
  function setupLoop() {
    const onSeek = vi.fn()
    const hook = renderHook(
      ({ currentTime }: { currentTime: number }) =>
        useClipEditor({
          mediaDetailsId: 1,
          duration: 600,
          currentTime,
          onSeek,
          onSaved: () => {},
        }),
      { initialProps: { currentTime: 100 } }
    )
    act(() => {
      hook.result.current.setStartTime(100)
      hook.result.current.setEndTime(110)
    })
    onSeek.mockClear()
    return { ...hook, onSeek }
  }

  it("starts from the selection's start", () => {
    const { result, onSeek } = setupLoop()

    act(() => result.current.toggleLoop())

    expect(onSeek).toHaveBeenCalledWith(100)
  })

  it("goes back to the start when the reported position leaves the selection", () => {
    const { result, rerender, onSeek } = setupLoop()
    act(() => result.current.toggleLoop())
    onSeek.mockClear()

    rerender({ currentTime: 105 })
    expect(onSeek).not.toHaveBeenCalled()

    rerender({ currentTime: 110.2 })
    expect(onSeek).toHaveBeenCalledWith(100)
  })

  it("leaves playback alone once the loop is stopped", () => {
    const { result, rerender, onSeek } = setupLoop()
    act(() => result.current.toggleLoop())
    act(() => result.current.stopLooping())
    onSeek.mockClear()

    rerender({ currentTime: 200 })

    expect(onSeek).not.toHaveBeenCalled()
  })
})
