import { describe, expect, it } from "vitest"
import {
  AUDIO_QUALITIES,
  durationSeconds,
  isAudioQuality,
  isVideoQuality,
  uploadedAfterTimestamp,
  VIDEO_QUALITIES,
} from "./choices"

describe("qualities", () => {
  it("match the server's DownloadQuality and AudioQuality enums", () => {
    expect(VIDEO_QUALITIES.map((q) => q.value)).toEqual(["BEST", "1440P", "1080P", "720P", "480P", "360P"])
    expect(AUDIO_QUALITIES.map((q) => q.value)).toEqual(["BEST", "128K", "96K", "64K", "48K"])
  })

  it("recognise only their own values", () => {
    expect(isVideoQuality("1080P")).toBe(true)
    expect(isVideoQuality("128K")).toBe(false)
    expect(isAudioQuality("128K")).toBe(true)
    expect(isAudioQuality("1080P")).toBe(false)
    expect(isAudioQuality(undefined)).toBe(false)
  })
})

describe("durationSeconds", () => {
  it.each([
    ["", null],
    ["  ", null],
    ["0", 0],
    ["1.5", 90],
    [" 10 ", 600],
    ["0.01", 1],
  ] as const)("%j minutes is %j seconds", (minutes, seconds) => {
    expect(durationSeconds(minutes)).toBe(seconds)
  })

  it.each(["-1", "abc", "1e400"])("%j is not a length", (minutes) => {
    expect(durationSeconds(minutes)).toBeUndefined()
  })
})

describe("uploadedAfterTimestamp", () => {
  it("is that day's local midnight, as the app's date picker sends it", () => {
    expect(uploadedAfterTimestamp("2026-01-15")).toBe(new Date(2026, 0, 15).toISOString())
  })

  it.each(["", "15/01/2026", "2026-1-5"])("%j is no date", (value) => {
    expect(uploadedAfterTimestamp(value)).toBeNull()
  })
})
