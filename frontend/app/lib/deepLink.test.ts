import { describe, expect, it } from "vitest"

import { parseDeepLink, stripDeepLink } from "./deepLink"

describe("parseDeepLink", () => {
  it("reads the media id and the second to start at", () => {
    expect(parseDeepLink("?media=12&t=754")).toEqual({ mediaId: 12, seconds: 754 })
  })

  it("starts at zero without a usable t", () => {
    expect(parseDeepLink("?media=12")).toEqual({ mediaId: 12, seconds: 0 })
    expect(parseDeepLink("?media=12&t=-5")).toEqual({ mediaId: 12, seconds: 0 })
    expect(parseDeepLink("?media=12&t=soon")).toEqual({ mediaId: 12, seconds: 0 })
  })

  it.each(["", "?t=754", "?media=", "?media=0", "?media=-3", "?media=1.5", "?media=abc", "?media=99999999999999999999"])(
    "ignores %j",
    (search) => {
      expect(parseDeepLink(search)).toBeNull()
    }
  )
})

describe("stripDeepLink", () => {
  it("removes only the deep-link params", () => {
    expect(stripDeepLink("http://nas:8000/?media=12&t=754")).toBe("/")
    expect(stripDeepLink("http://nas:8000/?x=1&media=12&t=754#top")).toBe("/?x=1#top")
  })

  it("leaves a URL without them unchanged", () => {
    expect(stripDeepLink("http://nas:8000/library?x=1")).toBe("/library?x=1")
  })
})
