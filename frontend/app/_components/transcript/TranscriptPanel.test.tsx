// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { TranscriptPanel } from "./TranscriptPanel"

afterEach(cleanup)

const blocks = [
  "The rocket engines ignite.",
  "Stage separation, then the rocket coasts.",
  "Orbit is reached.",
].map((text, id) => ({ id, start_time: id * 20, end_time: id * 20 + 20, text }))

function renderPanel(currentTime = 0) {
  const onSeek = vi.fn()
  render(<TranscriptPanel blocks={blocks} currentTime={currentTime} onSeek={onSeek} />)
  return onSeek
}

const search = () => screen.getByLabelText("Search this transcript") as HTMLInputElement
const passages = () => screen.getAllByRole("listitem").map((li) => li.textContent)

describe("TranscriptPanel", () => {
  it("lists every passage until something is typed", () => {
    renderPanel()

    expect(passages()).toHaveLength(3)
    expect(screen.queryByText(/passages$/)).toBeNull()
  })

  it("narrows to the passages containing every word, and says how many", () => {
    renderPanel()

    fireEvent.change(search(), { target: { value: "ROCKET coasts" } })

    expect(passages()).toEqual([expect.stringContaining("then the rocket coasts")])
    expect(screen.getByText("1 of 3 passages")).toBeTruthy()
    expect(screen.getAllByText(/rocket|coasts/, { selector: "mark" })).toHaveLength(2)
  })

  it("says so when nothing matches", () => {
    renderPanel()

    fireEvent.change(search(), { target: { value: "lunar" } })

    expect(screen.getByText(/No passages match/)).toBeTruthy()
  })

  it("clears with the × button, and with Escape", () => {
    renderPanel()

    fireEvent.change(search(), { target: { value: "orbit" } })
    fireEvent.click(screen.getByLabelText("Clear search"))
    expect(search().value).toBe("")
    expect(passages()).toHaveLength(3)

    fireEvent.change(search(), { target: { value: "orbit" } })
    fireEvent.keyDown(search(), { key: "Escape" })
    expect(search().value).toBe("")
  })

  it("seeks to a passage that is clicked, also from a filtered list", () => {
    const onSeek = renderPanel()

    fireEvent.change(search(), { target: { value: "orbit" } })
    fireEvent.click(screen.getByText(/Orbit/, { selector: "span, mark" }).closest("button")!)

    expect(onSeek).toHaveBeenCalledWith(40)
  })

  it("marks the passage that is playing", () => {
    renderPanel(25)

    const buttons = screen.getAllByRole("button")
    expect(buttons[1].className).toContain("bg-matrix/10")
    expect(buttons[0].className).not.toContain("bg-matrix/10")
  })
})
