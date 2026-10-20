// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { PlaybackControls, type QueueMode } from "./PlaybackControls"

afterEach(cleanup)

function renderControls(overrides: Partial<React.ComponentProps<typeof PlaybackControls>> = {}) {
  const props = {
    onPlayAll: vi.fn(),
    onShuffle: vi.fn(),
    resume: { checked: false, onChange: vi.fn() },
    ...overrides,
  }
  render(<PlaybackControls {...props} />)
  return props
}

const playAll = () => screen.getByText("Play All").closest("button")!
const shuffle = () => screen.getByText("Shuffle").closest("button")!
const resume = () => screen.getByText("Resume").closest("button")!

describe("PlaybackControls", () => {
  it.each<[QueueMode, string | null, string | null]>([
    ["off", "false", "false"],
    ["ordered", "true", "false"],
    ["shuffled", "false", "true"],
  ])("reflects queueMode %s as pressed state", (queueMode, pressedPlay, pressedShuffle) => {
    renderControls({ queueMode })

    expect(playAll().getAttribute("aria-pressed")).toBe(pressedPlay)
    expect(shuffle().getAttribute("aria-pressed")).toBe(pressedShuffle)
  })

  it("is a pair of plain actions on surfaces with no queue state", () => {
    renderControls()

    expect(playAll().getAttribute("aria-pressed")).toBeNull()
    expect(shuffle().getAttribute("aria-pressed")).toBeNull()
  })

  it("shows Resume on the bar as a button that turns on", () => {
    const props = renderControls()

    expect(screen.queryByLabelText("Playback options")).toBeNull()
    expect(resume().getAttribute("aria-pressed")).toBe("false")
    fireEvent.click(resume())
    expect(props.resume.onChange).toHaveBeenCalledWith(true)
  })

  it("shows Resume on as pressed, and turns it off", () => {
    const props = renderControls({ resume: { checked: true, onChange: vi.fn() } })

    expect(resume().getAttribute("aria-pressed")).toBe("true")
    fireEvent.click(resume())
    expect(props.resume.onChange).toHaveBeenCalledWith(false)
  })

  it("disables Resume while a queue is playing, without dropping the preference", () => {
    const props = renderControls({
      queueMode: "ordered",
      resume: { checked: true, disabled: true, onChange: vi.fn() },
    })

    expect(resume().hasAttribute("disabled")).toBe(true)
    expect(resume().getAttribute("aria-pressed")).toBe("true")

    fireEvent.click(resume())
    expect(props.resume.onChange).not.toHaveBeenCalled()
  })

  it("fires the playback callbacks", () => {
    const props = renderControls()

    fireEvent.click(playAll())
    fireEvent.click(shuffle())

    expect(props.onPlayAll).toHaveBeenCalledOnce()
    expect(props.onShuffle).toHaveBeenCalledOnce()
  })

  it("disables both actions when the surface has nothing to play", () => {
    renderControls({ disabled: true })

    expect(playAll().hasAttribute("disabled")).toBe(true)
    expect(shuffle().hasAttribute("disabled")).toBe(true)
  })
})
