import type { DownloadRequest, SubscribeRequest, TaskFeed } from "./api"

export type ListTasksRequest = { type: "listTasks" }

/** What the popup and settings page ask the background script to do. */
export type ExtensionRequest =
  | { type: "download"; request: DownloadRequest }
  | { type: "subscribe"; request: SubscribeRequest }
  | { type: "testConnection" }
  | ListTasksRequest

export function isExtensionRequest(value: unknown): value is ExtensionRequest {
  if (typeof value !== "object" || value === null) return false
  const { type } = value as { type?: unknown }
  return type === "download" || type === "subscribe" || type === "testConnection" || type === "listTasks"
}

/** Whether a reply to `listTasks` is a task feed, and not the background's generic failure. */
export function isTaskFeed(value: unknown): value is TaskFeed {
  return typeof value === "object" && value !== null && typeof (value as { ok?: unknown }).ok === "boolean"
}
