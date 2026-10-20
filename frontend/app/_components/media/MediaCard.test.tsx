// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import type { Download } from "@/app/types/DownloadsOptions"
import { MediaCard } from "./MediaCard"

afterEach(cleanup)

const row = (overrides: Partial<Download> = {}) =>
  ({
    media_details_id: 7,
    title: "A Talk",
    channel: "Lectures",
    media_type: "TRANSCRIPT",
    status: "COMPLETE",
    duration: 60,
    thumbnail_path: null,
    ...overrides,
  }) as Download

describe("MediaCard", () => {
  it("labels the card with its media type by default", () => {
    render(<MediaCard row={row({ media_type: "VIDEO" })} index={0} actions={null} />)

    expect(screen.getByText("VIDEO")).toBeTruthy()
  })

  it("takes the Transcripts view's origin label instead", () => {
    render(<MediaCard row={row()} index={0} actions={null} typeLabel="Transcript only" />)

    expect(screen.getByText("Transcript only")).toBeTruthy()
    expect(screen.queryByText("TRANSCRIPT")).toBeNull()
  })

  it("shows the kept thumbnail of a transcript-only item", () => {
    const { container } = render(
      <MediaCard row={row({ thumbnail_path: "/kept.jpg" })} index={0} actions={null} />,
    )

    expect(container.querySelector("img")?.getAttribute("src")).toContain("/media/7/thumbnail")
  })
})
