// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"

// Each passage formats its time once per render, so the spy counts row renders.
const formatTime = vi.hoisted(() => vi.fn())
vi.mock("@/app/utils", async (importOriginal) => {
  const utils = await importOriginal<typeof import("@/app/utils")>()
  formatTime.mockImplementation(utils.formatTime)
  return { ...utils, formatTime }
})

const { TranscriptPanel } = await import("./TranscriptPanel")

afterEach(() => {
  cleanup()
  formatTime.mockClear()
})

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

    fireEvent.click(screen.getByText("0:40"))
    expect(onSeek).toHaveBeenCalledTimes(2)
  })

  it("marks the passage that is playing", () => {
    renderPanel(25)

    const buttons = within(screen.getByRole("list")).getAllByRole("button")
    expect(buttons[1].className).toContain("bg-matrix/10")
    expect(buttons[0].className).not.toContain("bg-matrix/10")
  })

  it("redraws only the passages whose highlight moves as playback ticks", () => {
    const { rerender } = render(
      <TranscriptPanel blocks={blocks} currentTime={21} onSeek={() => {}} />
    )
    expect(formatTime).toHaveBeenCalledTimes(3)
    formatTime.mockClear()

    // Hosts pass a fresh onSeek with every tick.
    rerender(<TranscriptPanel blocks={blocks} currentTime={22} onSeek={() => {}} />)
    rerender(<TranscriptPanel blocks={blocks} currentTime={23} onSeek={() => {}} />)
    expect(formatTime).not.toHaveBeenCalled()

    rerender(<TranscriptPanel blocks={blocks} currentTime={41} onSeek={() => {}} />)
    expect(formatTime).toHaveBeenCalledTimes(2)
    formatTime.mockClear()

    fireEvent.change(search(), { target: { value: "rocket" } })
    expect(formatTime).toHaveBeenCalledTimes(2)
  })
})

describe("TranscriptPanel following playback", () => {
  // jsdom does no layout, so lay the list out by hand: a 200px view over 50px passages.
  const ROW = 50
  const VIEW = 200
  const layout = { scrollTop: 0, shift: 0 }
  const scrollTo = vi.fn((options: ScrollToOptions) => {
    layout.scrollTop = options.top ?? 0
  })
  const observed = new Set<() => void>()
  class FakeResizeObserver {
    callback: () => void
    constructor(callback: () => void) {
      this.callback = callback
    }
    observe() {
      observed.add(this.callback)
    }
    unobserve() {}
    disconnect() {
      observed.delete(this.callback)
    }
  }
  const resize = () => observed.forEach((callback) => callback())
  const stubbed = ["clientHeight", "scrollTop", "scrollTo"] as const
  const originals = stubbed.map((key) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, key))

  const box = (top: number, height: number) =>
    ({ top, bottom: top + height, left: 0, right: 0, x: 0, y: top, width: 0, height }) as DOMRect

  beforeEach(() => {
    layout.scrollTop = 0
    layout.shift = 0
    observed.clear()
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
      this: Element
    ) {
      if (this.tagName === "OL") return box(0, VIEW)
      const index = Number((this as HTMLElement).dataset.index)
      return box(index * ROW + layout.shift - layout.scrollTop, ROW)
    })
    Object.defineProperties(HTMLElement.prototype, {
      clientHeight: { configurable: true, get: () => VIEW },
      scrollTop: { configurable: true, get: () => layout.scrollTop },
      scrollTo: { configurable: true, value: scrollTo },
    })
    vi.stubGlobal("ResizeObserver", FakeResizeObserver)
  })

  afterEach(() => {
    vi.mocked(Element.prototype.getBoundingClientRect).mockRestore()
    vi.unstubAllGlobals()
    scrollTo.mockClear()
    stubbed.forEach((key, i) => {
      const original = originals[i]
      if (original) Object.defineProperty(HTMLElement.prototype, key, original)
      else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[key]
    })
  })

  const long = Array.from({ length: 100 }, (_, id) => ({
    id,
    start_time: id * 20,
    end_time: id * 20 + 20,
    text: `Passage ${id}.`,
  }))
  const at = (index: number) => index * 20 + 5

  function renderLong(index: number) {
    const onSeek = vi.fn()
    const { rerender } = render(
      <TranscriptPanel blocks={long} currentTime={at(index)} onSeek={onSeek} />
    )
    const playAt = (next: number) =>
      rerender(<TranscriptPanel blocks={long} currentTime={at(next)} onSeek={onSeek} />)
    return { onSeek, playAt }
  }

  const scrollListTo = (top: number) => {
    const list = screen.getByRole("list")
    fireEvent.wheel(list)
    layout.scrollTop = top
    fireEvent.scroll(list)
  }

  const follow = () => screen.getByRole("button", { name: "Follow" })
  const followOn = () => follow().getAttribute("aria-pressed") === "true"

  it("opens with the playing passage second from the top, however deep it is", () => {
    renderLong(60)

    // Passage 59 sits at 2950px, and goes to the top of the view at once.
    expect(followOn()).toBe(true)
    expect(scrollTo).toHaveBeenCalledTimes(1)
    expect(scrollTo).toHaveBeenCalledWith({ top: 2950, behavior: "instant" })
  })

  it("eases along with every passage as playback moves", () => {
    const { playAt } = renderLong(60)
    scrollTo.mockClear()

    playAt(61)
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 3000, behavior: "smooth" })

    playAt(62)
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 3050, behavior: "smooth" })
  })

  it("re-aligns once the rows it jumped among take their real height", () => {
    const { playAt } = renderLong(60)
    layout.shift = 120

    resize()
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 3070, behavior: "instant" })

    playAt(61)
    scrollTo.mockClear()
    resize()
    expect(scrollTo).not.toHaveBeenCalled()
  })

  it("leaves that to the user once they have started scrolling", () => {
    renderLong(60)
    layout.shift = 120

    fireEvent.wheel(screen.getByRole("list"))
    resize()

    expect(scrollTo).toHaveBeenCalledTimes(1)
  })

  it("turns Follow off at any scroll of the user's, until Follow is pressed again", () => {
    const { playAt } = renderLong(60)

    // Passage 60 is still in view.
    scrollListTo(2900)
    expect(followOn()).toBe(false)

    scrollTo.mockClear()
    playAt(62)
    expect(scrollTo).not.toHaveBeenCalled()

    fireEvent.click(follow())
    expect(followOn()).toBe(true)
    expect(scrollTo).toHaveBeenCalledWith({ top: 3050, behavior: "smooth" })

    scrollListTo(1000)
    fireEvent.click(follow())
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 3050, behavior: "instant" })
  })

  it("turns Follow off from its button without scrolling", () => {
    const { playAt } = renderLong(60)
    scrollTo.mockClear()

    fireEvent.click(follow())
    expect(followOn()).toBe(false)
    playAt(62)

    expect(scrollTo).not.toHaveBeenCalled()
  })

  it("keeps following through a tap that didn't scroll", () => {
    renderLong(60)
    const list = screen.getByRole("list")

    fireEvent.touchStart(list)
    fireEvent.touchEnd(list)
    fireEvent.pointerDown(list)
    fireEvent.pointerUp(list)
    // Rows growing out of their placeholders scroll the list too.
    fireEvent.scroll(list)

    expect(followOn()).toBe(true)
  })

  it("follows from a clicked passage, without first jumping back to the old one", () => {
    const { onSeek, playAt } = renderLong(60)
    scrollListTo(1000)
    scrollTo.mockClear()

    fireEvent.click(screen.getByText("Passage 21."))
    expect(onSeek).toHaveBeenCalledWith(420)
    expect(followOn()).toBe(true)
    // The host hasn't caught up with the seek yet.
    expect(scrollTo).not.toHaveBeenCalled()

    // Passage 20 is already at the top.
    playAt(21)
    expect(scrollTo).not.toHaveBeenCalled()

    playAt(22)
    expect(scrollTo).toHaveBeenCalledWith({ top: 1050, behavior: "smooth" })
  })

  it("holds still while searching, and Follow clears the search to go back", () => {
    const { playAt } = renderLong(60)

    fireEvent.change(search(), { target: { value: "passage" } })
    expect(followOn()).toBe(false)
    scrollTo.mockClear()
    scrollListTo(500)
    playAt(62)
    expect(scrollTo).not.toHaveBeenCalled()

    fireEvent.click(follow())
    expect(search().value).toBe("")
    expect(followOn()).toBe(true)
    expect(scrollTo).toHaveBeenCalledWith({ top: 3050, behavior: "instant" })

    fireEvent.change(search(), { target: { value: "passage" } })
    scrollListTo(500)
    scrollTo.mockClear()
    fireEvent.click(screen.getByLabelText("Clear search"))
    expect(scrollTo).toHaveBeenCalledWith({ top: 3050, behavior: "instant" })
  })
})
