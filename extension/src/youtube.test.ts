import { describe, expect, it } from "vitest"
import { classifyPage } from "./youtube"

// The examples mirror backend/tests/test_url_utils.py, so the two classifiers stay in step.
const WATCH = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
const PLAYLIST = "https://www.youtube.com/playlist?list=PLJ8cMiYb3G5fEBBRGG3cXJJTPSBJb2MLa"

describe("classifyPage: videos", () => {
  it.each([
    ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", WATCH],
    ["https://www.youtube.com/shorts/lYBUbBu4W08", "https://www.youtube.com/watch?v=lYBUbBu4W08"],
    ["https://youtu.be/dQw4w9WgXcQ", WATCH],
    ["https://www.youtube.com/embed/dQw4w9WgXcQ", WATCH],
    ["https://www.youtube.com/live/dQw4w9WgXcQ", WATCH],
    ["https://music.youtube.com/watch?v=dQw4w9WgXcQ", WATCH],
    ["https://m.youtube.com/watch?v=dQw4w9WgXcQ", WATCH],
    ["https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s", WATCH],
  ])("%s is a video", (url, video) => {
    expect(classifyPage(url)).toEqual({ video })
  })

  it("a watch URL with list= offers the video and the playlist", () => {
    const url =
      "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLJ8cMiYb3G5fEBBRGG3cXJJTPSBJb2MLa&index=2"
    expect(classifyPage(url)).toEqual({ video: WATCH, playlist: PLAYLIST })
  })

  it("treats a blank v= as absent, as parse_qs does", () => {
    expect(classifyPage("https://www.youtube.com/watch?v=&list=PLJ8cMiYb3G5fEBBRGG3cXJJTPSBJb2MLa")).toEqual({
      playlist: PLAYLIST,
    })
  })
})

describe("classifyPage: playlists", () => {
  it("canonicalizes a playlist page", () => {
    expect(classifyPage(PLAYLIST)).toEqual({ playlist: PLAYLIST })
  })

  it("ignores list= on other sites", () => {
    expect(classifyPage("https://example.com/c/SomeChannel?list=some-other-sites-list-id")).toEqual({})
  })
})

describe("classifyPage: channels", () => {
  it.each([
    ["https://www.youtube.com/@RickAstleyYT", "https://www.youtube.com/@RickAstleyYT"],
    [
      "https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw",
      "https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw",
    ],
    ["https://www.youtube.com/c/RickAstley", "https://www.youtube.com/c/RickAstley"],
    ["https://www.youtube.com/user/RickAstley", "https://www.youtube.com/user/RickAstley"],
    ["https://m.youtube.com/@RickAstleyYT/", "https://www.youtube.com/@RickAstleyYT"],
    ["https://www.youtube.com/@RickAstleyYT/videos?view=0#top", "https://www.youtube.com/@RickAstleyYT/videos"],
  ])("%s is a channel", (url, channel) => {
    expect(classifyPage(url)).toEqual({ channel })
  })

  it("keeps a channel ID's case", () => {
    expect(classifyPage("https://www.youtube.com/channel/UCabcDEF").channel).toBe(
      "https://www.youtube.com/channel/UCabcDEF"
    )
  })

  it("matches the server in not treating music.youtube.com paths as channels", () => {
    expect(classifyPage("https://music.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw")).toEqual({})
  })

  it("a video is not a channel", () => {
    expect(classifyPage(WATCH).channel).toBeUndefined()
  })
})

describe("classifyPage: everything else", () => {
  it.each([
    "https://example.com/v1abc23-some-video.html",
    "https://x.com/user/status/1234567890",
    "https://example.com/@channel:1/video-title:2",
    "https://example.com/some/video/path",
    "https://rumble.com/c/SomeChannel",
    "https://www.youtube.com/",
    "https://www.youtube.com/feed/subscriptions",
    "chrome://extensions",
    "about:blank",
    "",
    "not a url",
  ])("%s offers nothing", (url) => {
    expect(classifyPage(url)).toEqual({})
  })
})
