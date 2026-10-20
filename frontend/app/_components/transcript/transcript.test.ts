import { describe, expect, it } from "vitest"
import {
  activeBlockIndex,
  filterTranscriptBlocks,
  pageKindFor,
  parsePlayerInfo,
  positionSaveDue,
  resumePosition,
  sourceUrlAt,
  transcriptButtonState,
  transcriptQueryTerms,
  type TranscriptDocument,
} from "./transcript"

const doc = (
  media: Partial<TranscriptDocument["media"]>,
  youtube_id: string | null = "abc123def45"
): TranscriptDocument => ({
  media: {
    id: 1,
    url: "https://www.youtube.com/watch?v=abc123def45",
    title: "T",
    channel: "C",
    media_type: "TRANSCRIPT",
    status: "COMPLETE",
    duration: 60,
    release_timestamp: null,
    thumbnail_path: null,
    playable: false,
    rating: null,
    access_count: 0,
    playback_position: null,
    owner_id: 1,
    transcript_task_status: null,
    transcript_task_progress: null,
    ...media,
  },
  youtube_id,
  blocks: [],
})

describe("pageKindFor", () => {
  it.each([
    [{ media_type: "VIDEO", status: "COMPLETE", file_path: "/a.mp4" }, "video"],
    [{ media_type: "AUDIO", status: "COMPLETE", file_path: "/a.m4a" }, "audio"],
    // A search hit carries no file_path; a COMPLETE library row has a file.
    [{ media_type: "VIDEO", status: "COMPLETE" }, "video"],
    [{ media_type: "TRANSCRIPT", status: "COMPLETE" }, "embed"],
    [{ media_type: "VIDEO", status: "DELETED" }, "embed"],
    [{ media_type: "AUDIO", status: "COMPLETE", file_path: null }, "embed"],
  ])("opens %o on the %s page", (media, kind) => {
    expect(pageKindFor(media)).toBe(kind)
  })
})

describe("transcriptButtonState", () => {
  const owner = { id: 1 }
  const blocks = [{ id: 1, start_time: 0, end_time: 20, text: "hi" }]
  const withBlocks = { ...doc({ playable: true }), blocks }

  it("shows a transcript that exists, to anyone", () => {
    expect(transcriptButtonState(withBlocks, { id: 9 }, false)).toBe("show")
  })

  it("offers to generate one to the owner of a downloaded file", () => {
    expect(transcriptButtonState(doc({ playable: true }), owner, false)).toBe("generate")
  })

  it("offers it to an admin who isn't the owner", () => {
    expect(transcriptButtonState(doc({ playable: true }), { id: 9, is_admin: true }, false)).toBe(
      "generate"
    )
  })

  it("offers nothing to someone else, who the server would refuse", () => {
    expect(transcriptButtonState(doc({ playable: true }), { id: 9 }, false)).toBe("none")
  })

  it("offers nothing without a file to transcribe", () => {
    expect(transcriptButtonState(doc({ playable: false }), owner, false)).toBe("none")
  })

  it.each(["QUEUED", "IN_PROGRESS", "RETRY"])("is working while the job is %s", (status) => {
    const working = doc({ playable: true, transcript_task_status: status })
    expect(transcriptButtonState(working, owner, false)).toBe("working")
  })

  it("hides everything offline", () => {
    expect(transcriptButtonState(withBlocks, owner, true)).toBe("none")
  })
})

describe("transcript search", () => {
  const blocks = [
    "The Rocket engines ignite.",
    "Stage separation, then the rocket coasts.",
    "Orbit is reached!",
  ].map((text, id) => ({ id, start_time: id * 20, end_time: id * 20 + 20, text }))

  it("splits, lowercases and trims punctuation off the typed words", () => {
    expect(transcriptQueryTerms("  Rocket,  ENGINES!  rocket ")).toEqual(["rocket", "engines"])
  })

  it("keeps every passage for an empty search", () => {
    expect(filterTranscriptBlocks(blocks, [])).toHaveLength(3)
  })

  it("keeps passages containing every word, in any case", () => {
    const shown = filterTranscriptBlocks(blocks, transcriptQueryTerms("rocket engines"))
    expect(shown.map((s) => s.index)).toEqual([0])
  })

  it("matches inside words, so a partial word narrows as it is typed", () => {
    const shown = filterTranscriptBlocks(blocks, transcriptQueryTerms("roc"))
    expect(shown.map((s) => s.index)).toEqual([0, 1])
  })

  it("keeps each passage's place in the whole transcript", () => {
    expect(filterTranscriptBlocks(blocks, ["orbit"])).toEqual([{ block: blocks[2], index: 2 }])
  })
})

describe("activeBlockIndex", () => {
  const blocks = [0, 20, 40].map((start, id) => ({
    id,
    start_time: start,
    end_time: start + 20,
    text: String(id),
  }))

  it.each([
    [0, 0],
    [19.9, 0],
    [20, 1],
    [95, 2],
  ])("at %ss is block %i", (seconds, index) => {
    expect(activeBlockIndex(blocks, seconds)).toBe(index)
  })
})

describe("sourceUrlAt", () => {
  it("keeps the video and adds the whole-second timestamp", () => {
    expect(sourceUrlAt("https://www.youtube.com/watch?v=abc", 75.6)).toBe(
      "https://www.youtube.com/watch?v=abc&t=75"
    )
  })

  it("leaves an unparseable URL alone", () => {
    expect(sourceUrlAt("not a url", 5)).toBe("not a url")
  })
})

describe("parsePlayerInfo", () => {
  it("reads the position from a player message", () => {
    const message = JSON.stringify({ event: "infoDelivery", info: { currentTime: 12.5 } })
    expect(parsePlayerInfo(message)).toEqual({ currentTime: 12.5 })
  })

  it("reads whether it is playing", () => {
    expect(parsePlayerInfo({ info: { playerState: 1 } })).toEqual({ playing: true })
    expect(parsePlayerInfo({ info: { playerState: 2 } })).toEqual({ playing: false })
  })

  it.each([["not json"], [{ event: "onReady" }], [null]])("ignores %o", (message) => {
    expect(parsePlayerInfo(message)).toBeNull()
  })
})

describe("positionSaveDue", () => {
  it("saves the first position", () => {
    expect(positionSaveDue(null, 40)).toBe(true)
  })

  it("then waits for 5 seconds of movement, either way", () => {
    expect(positionSaveDue(40, 44)).toBe(false)
    expect(positionSaveDue(40, 45)).toBe(true)
    expect(positionSaveDue(40, 30)).toBe(true)
  })
})

describe("resumePosition", () => {
  it("resumes where it was left", () => {
    expect(resumePosition(doc({ playback_position: 312, duration: 600 }).media)).toBe(312)
  })

  it("starts at the beginning when never played", () => {
    expect(resumePosition(doc({ playback_position: null }).media)).toBe(0)
  })

  it("restarts a video left in its last 30 seconds", () => {
    expect(resumePosition(doc({ playback_position: 580, duration: 600 }).media)).toBe(0)
  })

  it("resumes when the duration isn't known", () => {
    expect(resumePosition(doc({ playback_position: 580, duration: null }).media)).toBe(580)
  })
})
