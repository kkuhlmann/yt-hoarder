// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, renderHook, waitFor } from "@testing-library/react"

const get = vi.fn()
const playableRecords = vi.fn()
const toastError = vi.fn()

vi.mock("axios", () => {
  const axios = {
    get: (...args: unknown[]) => get(...args),
    defaults: { withCredentials: false },
    interceptors: { response: { use: () => {} } },
    isCancel: () => false,
    isAxiosError: (error: unknown) => Boolean((error as { isAxiosError?: boolean })?.isAxiosError),
  }
  return { default: axios, isCancel: axios.isCancel, isAxiosError: axios.isAxiosError }
})
vi.mock("@/app/lib/offlineLibrary", () => ({ playableRecords: () => playableRecords() }))
vi.mock("react-hot-toast", () => ({ default: { error: (msg: string) => toastError(msg) } }))

const { useDeepLinkPlayback } = await import("./useDeepLinkPlayback")

const VIDEO = {
  id: 12,
  media_type: "VIDEO",
  title: "Lunar Lander Update",
  channel: "NASA",
  url: "https://www.youtube.com/watch?v=abc",
  duration: 900,
  status: "COMPLETE",
  file_path: "/mnt/video/abc.mp4",
}

// RTL's automatic cleanup never registers with globals off — see .claude/rules/frontend.md.
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function setup(deepLink: { mediaId: number; seconds: number } | null, offlineMode = false) {
  const handlers = { onAudio: vi.fn(), onVideo: vi.fn(), onHandled: vi.fn() }
  renderHook(() => useDeepLinkPlayback(deepLink, offlineMode, handlers))
  return handlers
}

describe("useDeepLinkPlayback", () => {
  it("opens a video at the linked second, once", async () => {
    get.mockResolvedValue({ data: VIDEO })
    const handlers = setup({ mediaId: 12, seconds: 754 })

    await waitFor(() => expect(handlers.onHandled).toHaveBeenCalledOnce())
    expect(get).toHaveBeenCalledWith(expect.stringMatching(/\/media-details\/12$/), expect.anything())
    expect(handlers.onVideo).toHaveBeenCalledWith(VIDEO, 754)
    expect(handlers.onAudio).not.toHaveBeenCalled()
  })

  it("routes audio to the audio player", async () => {
    const audio = { ...VIDEO, media_type: "AUDIO" }
    get.mockResolvedValue({ data: audio })
    const handlers = setup({ mediaId: 12, seconds: 30 })

    await waitFor(() => expect(handlers.onHandled).toHaveBeenCalledOnce())
    expect(handlers.onAudio).toHaveBeenCalledWith(audio, 30)
  })

  it("says so when the media is missing or not shared with the user", async () => {
    get.mockRejectedValue({ isAxiosError: true, response: { status: 404 } })
    const handlers = setup({ mediaId: 99, seconds: 0 })

    await waitFor(() => expect(handlers.onHandled).toHaveBeenCalledOnce())
    expect(toastError).toHaveBeenCalledWith("That video isn't in your library")
    expect(handlers.onVideo).not.toHaveBeenCalled()
  })

  it("does not try to play media whose file is gone", async () => {
    get.mockResolvedValue({ data: { ...VIDEO, status: "DELETED", file_path: null } })
    const handlers = setup({ mediaId: 12, seconds: 0 })

    await waitFor(() => expect(handlers.onHandled).toHaveBeenCalledOnce())
    expect(toastError).toHaveBeenCalledWith("Media file not available")
    expect(handlers.onVideo).not.toHaveBeenCalled()
  })

  it("plays from the device while offline, without asking the server", async () => {
    playableRecords.mockResolvedValue([VIDEO])
    const handlers = setup({ mediaId: 12, seconds: 5 }, true)

    await waitFor(() => expect(handlers.onHandled).toHaveBeenCalledOnce())
    expect(get).not.toHaveBeenCalled()
    expect(handlers.onVideo).toHaveBeenCalledWith(VIDEO, 5)
  })

  it("explains an offline link to something not downloaded", async () => {
    playableRecords.mockResolvedValue([])
    const handlers = setup({ mediaId: 12, seconds: 5 }, true)

    await waitFor(() => expect(handlers.onHandled).toHaveBeenCalledOnce())
    expect(toastError).toHaveBeenCalledWith("That video isn't downloaded to this device")
  })

  it("does nothing without a link", () => {
    const handlers = setup(null)

    expect(get).not.toHaveBeenCalled()
    expect(handlers.onHandled).not.toHaveBeenCalled()
  })
})
