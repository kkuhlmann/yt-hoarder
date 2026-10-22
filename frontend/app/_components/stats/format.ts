import type { Granularity } from "@/app/types/StatsOptions"

export function formatCount(count: number): string {
  if (count < 1000) return count.toLocaleString()
  const thousands = count / 1000
  const formatted = thousands >= 100 ? thousands.toFixed(0) : thousands.toFixed(1).replace(/\.0$/, "")
  return `${formatted}k`
}

export function formatShare(part: number, whole: number): string {
  if (whole <= 0) return ""
  const percent = (part / whole) * 100
  if (percent === 0) return "0%"
  if (percent < 0.1) return "<0.1%"
  return `${percent < 10 ? percent.toFixed(1) : percent.toFixed(0)}%`
}

export function formatDurationLong(seconds: number): string {
  if (!seconds || seconds <= 0) return "0 hours"
  const hours = seconds / 3600
  if (hours >= 1) return `${hours.toFixed(1)} hours`
  const mins = seconds / 60
  return `${mins.toFixed(0)} min`
}

// Recharts types formatter args as ReactNode/ValueType, so this takes unknown
// and guards rather than forcing a cast at each chart call site.
export function formatPeriodLabel(period: unknown, granularity: Granularity): string {
  if (typeof period !== "string") return ""
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  if (granularity === "month") {
    const [year, m] = period.split("-")
    return `${months[parseInt(m) - 1]} ${year.slice(2)}`
  }
  // day or week: "2026-02-08" → "Feb 8"
  const [, m, d] = period.split("-")
  const prefix = granularity === "week" ? "W/O " : ""
  return `${prefix}${months[parseInt(m) - 1]} ${parseInt(d)}`
}
