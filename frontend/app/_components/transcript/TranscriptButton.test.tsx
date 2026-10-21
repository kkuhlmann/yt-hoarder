// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import type { MediaTranscript } from "@/app/_hooks/useMediaTranscript"
import { TranscriptButton } from "./TranscriptButton"
import type { TranscriptDocument } from "./transcript"

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

const doc = (media: Partial<TranscriptDocument["media"]> = {}) =>
  ({
    media: { transcript_task_status: null, transcript_task_progress: null, ...media },
    youtube_id: null,
    blocks: [],
  }) as unknown as TranscriptDocument

function renderButton(overrides: Partial<MediaTranscript>) {
  const transcript: MediaTranscript = {
    doc: doc(),
    failed: false,
    buttonState: "none",
    generate: vi.fn(),
    patchMedia: vi.fn(),
    ...overrides,
  }
  render(<TranscriptButton transcript={transcript} />)
  return transcript
}

describe("TranscriptButton", () => {
  it("toggles the panel, pressed while it shows", () => {
    renderButton({ buttonState: "show" })
    const button = screen.getByText("Transcript").closest("button")!

    expect(button.getAttribute("aria-pressed")).toBe("false")
    fireEvent.click(button)
    expect(localStorage.getItem("showTranscript")).toBe("1")
    expect(button.getAttribute("aria-pressed")).toBe("true")
  })

  it("starts a transcription when there is none", () => {
    const t = renderButton({ buttonState: "generate" })

    fireEvent.click(screen.getByText("Generate transcript"))

    expect(t.generate).toHaveBeenCalledOnce()
  })

  it("shows a running job's progress, and can't be pressed", () => {
    renderButton({
      buttonState: "working",
      doc: doc({ transcript_task_status: "IN_PROGRESS", transcript_task_progress: 42.7 }),
    })

    const button = screen.getByText("Transcribing… 42%").closest("button")!
    expect(button.hasAttribute("disabled")).toBe(true)
  })

  it("says a queued job is queued", () => {
    renderButton({ buttonState: "working", doc: doc({ transcript_task_status: "QUEUED" }) })

    expect(screen.getByText("Transcript queued")).toBeTruthy()
  })

  it("renders nothing when there is nothing to offer", () => {
    renderButton({ buttonState: "none" })

    expect(screen.queryByRole("button")).toBeNull()
  })
})
