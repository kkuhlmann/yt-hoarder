// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { ScopeSelector } from "./ScopeSelector"

afterEach(cleanup)

describe("ScopeSelector", () => {
  it.each([
    ["COMPLETE", "Library"],
    ["TRANSCRIPTS", "Transcripts"],
    ["SKIPPED", "Skipped"],
    ["DELETED", "Deleted"],
  ])("labels %s as %s", (value, label) => {
    render(<ScopeSelector value={value} onChange={() => {}} />)
    expect(screen.getByRole("button").textContent).toBe(label)
  })

  it("falls back to Library for an unrecognised status", () => {
    render(<ScopeSelector value="RESOLVING" onChange={() => {}} />)
    expect(screen.getByRole("button").textContent).toBe("Library")
  })

  it("tints the trigger only outside the default scope", () => {
    const { rerender } = render(<ScopeSelector value="COMPLETE" onChange={() => {}} />)
    expect(screen.getByRole("button").className).toContain("text-text-muted")

    rerender(<ScopeSelector value="DELETED" onChange={() => {}} />)
    expect(screen.getByRole("button").className).toContain("text-status-error")
  })

  it("reports the picked scope and closes", () => {
    const onChange = vi.fn()
    render(<ScopeSelector value="COMPLETE" onChange={onChange} />)

    fireEvent.click(screen.getByRole("button"))
    fireEvent.click(screen.getByText("Skipped"))

    expect(onChange).toHaveBeenCalledWith("SKIPPED")
    expect(screen.queryByText("Deleted")).toBeNull()
  })
})
