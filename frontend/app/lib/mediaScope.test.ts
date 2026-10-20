import { describe, expect, it } from "vitest"
import {
  TRANSCRIPTS_SCOPE,
  isRatable,
  mediaTypeLabel,
  scopeParams,
  transcriptOrigin,
} from "./mediaScope"

describe("scopeParams", () => {
  it("sends a status scope as the status", () => {
    expect(scopeParams("DELETED")).toEqual({ status: "DELETED" })
  })

  it("sends the Transcripts scope as has_transcript, with no status", () => {
    // A status would narrow it back to one slice; the view spans every status.
    expect(scopeParams(TRANSCRIPTS_SCOPE)).toEqual({ has_transcript: true })
  })

  it("sends nothing for no scope, leaving the server default", () => {
    expect(scopeParams(null)).toEqual({})
  })
})

describe("transcriptOrigin", () => {
  it.each([
    [{ media_type: "TRANSCRIPT", status: "COMPLETE" }, "transcript-only"],
    [{ media_type: "VIDEO", status: "COMPLETE" }, "library"],
    [{ media_type: "AUDIO", status: "DELETED" }, "deleted"],
  ])("labels %o as %s", (row, origin) => {
    expect(transcriptOrigin(row)).toBe(origin)
  })
})

describe("mediaTypeLabel", () => {
  it("names the media type in the Library", () => {
    expect(mediaTypeLabel("COMPLETE", { media_type: "VIDEO", status: "COMPLETE" })).toBe("VIDEO")
  })

  it("says where the transcript lives in the Transcripts view", () => {
    const deleted = { media_type: "VIDEO", status: "DELETED" }
    expect(mediaTypeLabel(TRANSCRIPTS_SCOPE, deleted)).toBe("Deleted")
  })
})

describe("isRatable", () => {
  it.each([
    ["COMPLETE", "COMPLETE", true],
    ["DELETED", "DELETED", false],
    [TRANSCRIPTS_SCOPE, "COMPLETE", true],
    [TRANSCRIPTS_SCOPE, "DELETED", false],
  ])("in %s, a %s row rates: %s", (scope, status, ratable) => {
    expect(isRatable(scope, { status })).toBe(ratable)
  })
})
