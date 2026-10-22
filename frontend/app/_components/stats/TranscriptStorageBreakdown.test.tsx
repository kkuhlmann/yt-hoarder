// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import type { TranscriptStorage, TranscriptStorageByChannel } from "@/app/types/StatsOptions"
import { TranscriptStorageBreakdown } from "./TranscriptStorageBreakdown"

afterEach(cleanup)

const MB = 1024 * 1024

function channel(name: string, mb: number, transcripts = 2, blocks = 40): TranscriptStorageByChannel {
  const total = mb * MB
  return {
    channel: name,
    total_bytes: total,
    text_bytes: total / 4,
    embedding_bytes: (total * 3) / 4,
    transcript_count: transcripts,
    block_count: blocks,
  }
}

function storageOf(by_channel: TranscriptStorageByChannel[], database_bytes: number | null = null): TranscriptStorage {
  const sum = (key: "total_bytes" | "text_bytes" | "embedding_bytes") =>
    by_channel.reduce((acc, c) => acc + c[key], 0)
  return {
    total_bytes: sum("total_bytes"),
    text_bytes: sum("text_bytes"),
    embedding_bytes: sum("embedding_bytes"),
    database_bytes,
    by_channel,
  }
}

function manyChannels(count: number): TranscriptStorageByChannel[] {
  return Array.from({ length: count }, (_, i) => channel(`Channel ${i + 1}`, count - i))
}

function show(props: { storage?: TranscriptStorage | null; loading?: boolean; failed?: boolean }) {
  return render(
    <TranscriptStorageBreakdown
      storage={props.storage ?? null}
      loading={props.loading ?? false}
      failed={props.failed ?? false}
      color="#3b82f6"
    />
  )
}

const channelRows = () => screen.queryAllByText(/^Channel \d+$/)
const toggle = () => screen.queryByRole("button")

describe("TranscriptStorageBreakdown", () => {
  it("shows the total and how it splits between text and embeddings", () => {
    show({ storage: storageOf([channel("NASA", 4), channel("Lectures", 2)]) })

    expect(screen.getByText("6.0 MB")).not.toBeNull()
    expect(screen.getByText("1.5 MB")).not.toBeNull()
    expect(screen.getByText("4.5 MB")).not.toBeNull()
    expect(screen.getByText("25% of transcript storage")).not.toBeNull()
    expect(screen.getByText("75% of transcript storage")).not.toBeNull()
  })

  it("gives the share of the database only when the server sends its size", () => {
    show({ storage: storageOf([channel("NASA", 4), channel("Lectures", 2)]) })
    expect(screen.queryByText(/database$/)).toBeNull()

    cleanup()
    show({ storage: storageOf([channel("NASA", 4), channel("Lectures", 2)], 600 * MB) })
    expect(screen.getByText("1.0% of 600 MB database")).not.toBeNull()
  })

  it("counts each channel's transcripts and blocks under its bar", () => {
    show({ storage: storageOf([channel("NASA", 4, 3, 120), channel("Lectures", 2, 1, 1)]) })

    expect(screen.getByText(/^3 transcripts · 120 blocks$/)).not.toBeNull()
    expect(screen.getByText(/^1 transcript · 1 block$/)).not.toBeNull()
  })

  it("lists the top ten channels until asked for all of them", () => {
    show({ storage: storageOf(manyChannels(12)) })
    expect(channelRows()).toHaveLength(10)
    expect(toggle()?.textContent).toBe("Show all 12 channels")
    expect(toggle()?.getAttribute("aria-expanded")).toBe("false")

    fireEvent.click(toggle()!)
    expect(channelRows()).toHaveLength(12)
    expect(toggle()?.textContent).toBe("Show top 10")
    expect(toggle()?.getAttribute("aria-expanded")).toBe("true")

    fireEvent.click(toggle()!)
    expect(channelRows()).toHaveLength(10)
  })

  it("offers no toggle when every channel already fits", () => {
    show({ storage: storageOf(manyChannels(10)) })

    expect(channelRows()).toHaveLength(10)
    expect(toggle()).toBeNull()
  })

  it("leaves out the channel list when there is only one channel to list", () => {
    show({ storage: storageOf([channel("NASA", 4)]) })

    expect(screen.queryByText("Storage by Channel")).toBeNull()
    expect(screen.getByText("4.0 MB")).not.toBeNull()
  })

  it("says it is measuring until the first figures arrive", () => {
    show({ loading: true })

    expect(screen.getByText("Measuring database storage…")).not.toBeNull()
  })

  it("keeps the previous figures on screen while a refetch runs", () => {
    show({ storage: storageOf([channel("NASA", 4), channel("Lectures", 2)]), loading: true })

    expect(screen.queryByText("Measuring database storage…")).toBeNull()
    expect(screen.getByText("6.0 MB")).not.toBeNull()
  })

  it("reports a failure rather than showing figures for the wrong filter", () => {
    show({ storage: storageOf([channel("NASA", 4), channel("Lectures", 2)]), failed: true })

    expect(screen.getByText("Couldn't measure database storage")).not.toBeNull()
    expect(screen.queryByText("6.0 MB")).toBeNull()
  })

  it("goes back to measuring when a retry follows a failure", () => {
    show({ failed: true, loading: true })

    expect(screen.getByText("Measuring database storage…")).not.toBeNull()
  })

  it("renders nothing when there are no transcripts", () => {
    const { container } = show({ storage: storageOf([]) })

    expect(container.innerHTML).toBe("")
  })
})
