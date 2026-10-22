import { describe, expect, it } from "vitest"
import { formatCount, formatDurationLong, formatPeriodLabel, formatShare } from "./format"

describe("formatShare", () => {
  it.each([
    [1, 0, ""],
    [1, -5, ""],
    [0, 100, "0%"],
    [1, 10_000, "<0.1%"],
    [1, 1000, "0.1%"],
    [45, 1000, "4.5%"],
    [7, 10, "70%"],
    [1, 1, "100%"],
  ])("formatShare(%o, %o) -> %s", (part, whole, expected) => {
    expect(formatShare(part, whole)).toBe(expected)
  })
})

describe("formatCount", () => {
  it.each([
    [0, "0"],
    [500, "500"],
    [999, "999"],
  ])("formatCount(%o) -> %s (verbatim below 1000)", (input, expected) => {
    expect(formatCount(input)).toBe(expected)
  })

  it.each([
    [1000, "1k"],
    [1500, "1.5k"],
    [2000, "2k"],
    [42000, "42k"],
  ])("formatCount(%o) -> %s (strips a trailing .0)", (input, expected) => {
    expect(formatCount(input)).toBe(expected)
  })

  it.each([
    [100000, "100k"],
    [250000, "250k"],
  ])("formatCount(%o) -> %s (>=100k rounds to whole thousands)", (input, expected) => {
    expect(formatCount(input)).toBe(expected)
  })

  it("has no millions branch: values well above 100k just keep counting whole thousands", () => {
    expect(formatCount(1_500_000)).toBe("1500k")
  })
})

describe("formatDurationLong", () => {
  it.each([
    [0, "0 hours"],
    [-100, "0 hours"],
  ])("formatDurationLong(%o) -> %s", (input, expected) => {
    expect(formatDurationLong(input)).toBe(expected)
  })

  it.each([
    [120, "2 min"],
    [1800, "30 min"],
  ])("formatDurationLong(%o) -> %s (under an hour)", (input, expected) => {
    expect(formatDurationLong(input)).toBe(expected)
  })

  it.each([
    [3600, "1.0 hours"],
    [5400, "1.5 hours"],
    [7200, "2.0 hours"],
  ])("formatDurationLong(%o) -> %s (an hour or more, decimal not stripped)", (input, expected) => {
    expect(formatDurationLong(input)).toBe(expected)
  })
})

describe("formatPeriodLabel", () => {
  it.each([null, undefined, 42, {}])("returns an empty string for non-string input %o", (period) => {
    expect(formatPeriodLabel(period, "day")).toBe("")
  })

  it("renders a month period as \"Mon YY\"", () => {
    expect(formatPeriodLabel("2026-02", "month")).toBe("Feb 26")
  })

  it("renders a day period as \"Mon D\"", () => {
    expect(formatPeriodLabel("2026-02-08", "day")).toBe("Feb 8")
  })

  it("drops the leading zero from a single-digit day", () => {
    expect(formatPeriodLabel("2026-02-01", "day")).toBe("Feb 1")
  })

  it("prefixes a week period with \"W/O \"", () => {
    expect(formatPeriodLabel("2026-02-08", "week")).toBe("W/O Feb 8")
  })
})
