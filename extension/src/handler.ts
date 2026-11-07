import {
  describeDownload,
  describeFailure,
  describeIdentity,
  describeSubscription,
  describeTasks,
  downloadBody,
  originPattern,
  resolveConnection,
  sendRequest,
  subscribeBody,
  type Outcome,
  type TaskFeed,
} from "./api"
import type { ExtensionRequest, ListTasksRequest } from "./messages"
import type { Settings } from "./settings"

type FetchLike = (input: string, init: RequestInit) => Promise<Response>

export type HandlerDeps = {
  settings: {
    load(): Promise<Settings>
    save(update: Partial<Settings>): Promise<void>
  }
  hasPermission(pattern: string): Promise<boolean>
  fetch: FetchLike
}

export const SETUP_MESSAGE = "Set the server URL and token in the extension's settings first."

function error(message: string): Outcome {
  return { tone: "error", message }
}

function feedFailure(message: string, fatal: boolean): TaskFeed {
  return { ok: false, message, fatal, retryAfterSeconds: null }
}

/** Each kind of request has its own kind of reply. */
export type Handler = {
  (message: ListTasksRequest): Promise<TaskFeed>
  (message: Exclude<ExtensionRequest, ListTasksRequest>): Promise<Outcome>
  (message: ExtensionRequest): Promise<Outcome | TaskFeed>
}

/** The background script's work: every request to the server goes through here. */
export function createHandler(deps: HandlerDeps): Handler {
  async function permissionProblem(url: string): Promise<string | null> {
    if (await deps.hasPermission(originPattern(url))) return null
    return (
      `The extension isn't allowed to contact ${new URL(url).origin} yet. ` +
      "Open its settings and click Save & test."
    )
  }

  async function testConnection(settings: Settings): Promise<Outcome> {
    if (!settings.serverUrl || !settings.token) return error(SETUP_MESSAGE)
    const problem = await permissionProblem(settings.serverUrl)
    if (problem) return error(problem)

    const connection = await resolveConnection(deps.fetch, settings.serverUrl, settings.token)
    if (!connection.ok) {
      await deps.settings.save({ apiBase: "" })
      return error(connection.message)
    }
    await deps.settings.save({ apiBase: connection.apiBase })
    return { tone: "success", message: describeIdentity(connection.identity) }
  }

  async function listTasks(settings: Settings): Promise<TaskFeed> {
    const { apiBase, token } = settings
    // Setup problems won't fix themselves between polls, so the caller is told to stop.
    if (!apiBase || !token) return feedFailure(SETUP_MESSAGE, true)
    const problem = await permissionProblem(apiBase)
    if (problem) return feedFailure(problem, true)

    try {
      return describeTasks(await sendRequest(deps.fetch, "GET", `${apiBase}/extension/tasks`, token))
    } catch (failure) {
      return feedFailure(describeFailure(failure, new URL(apiBase).origin), false)
    }
  }

  async function handle(message: ExtensionRequest): Promise<Outcome | TaskFeed> {
    const settings = await deps.settings.load()
    if (message.type === "testConnection") return testConnection(settings)
    if (message.type === "listTasks") return listTasks(settings)

    const { apiBase, token } = settings
    if (!apiBase || !token) return error(SETUP_MESSAGE)
    const problem = await permissionProblem(apiBase)
    if (problem) return error(problem)

    try {
      if (message.type === "download") {
        const body = downloadBody(message.request)
        return describeDownload(await sendRequest(deps.fetch, "POST", `${apiBase}/ytdl/`, token, body))
      }
      const body = subscribeBody(message.request)
      return describeSubscription(
        await sendRequest(deps.fetch, "POST", `${apiBase}/subscriptions`, token, body)
      )
    } catch (failure) {
      return error(describeFailure(failure, new URL(apiBase).origin))
    }
  }

  return handle as Handler
}
