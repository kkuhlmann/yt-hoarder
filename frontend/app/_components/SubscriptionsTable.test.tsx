// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"

import type { SubscriptionType } from "@/app/types/SubscriptionsOptions"

const post = vi.fn()
const startJobPolling = vi.fn()
const toastError = vi.fn()

vi.mock("axios", () => {
  const axios = {
    post: (...args: unknown[]) => post(...args),
    put: vi.fn(),
    defaults: { withCredentials: false },
    interceptors: { response: { use: () => {} } },
    isCancel: () => false,
    isAxiosError: () => false,
  }
  return { default: axios, isCancel: axios.isCancel, isAxiosError: axios.isAxiosError }
})
vi.mock("react-hot-toast", () => ({
  default: {
    success: vi.fn(),
    error: (...args: unknown[]) => toastError(...args),
    loading: vi.fn(),
  },
}))
vi.mock("@/app/context/AuthContext", () => ({
  useAuth: () => ({ user: { id: 1, is_admin: false } }),
}))
vi.mock("@/app/jobStatusService", () => ({
  startJobPolling: (...args: unknown[]) => startJobPolling(...args),
}))
vi.mock("@/app/lib/offlineSync", () => ({ subscriptionSource: (id: number) => ({ id }) }))
vi.mock("@/app/_components/CollectionOfflineButton", () => ({
  CollectionOfflineButton: () => null,
}))
vi.mock("@/app/_components/ShareDialog", () => ({ ShareDialog: () => null }))
vi.mock("@/app/_components/EditSubscriptionDialog", () => ({
  EditSubscriptionDialog: () => null,
}))

const { SubscriptionsTable } = await import("./SubscriptionsTable")

const CHECK = "Check for new videos now"

const sub = (id: number, user_id: number): SubscriptionType =>
  ({
    id,
    user_id,
    url: `https://www.youtube.com/@channel${id}`,
    channel: `Channel ${id}`,
    enabled: true,
    audio_only: false,
    media_type: "VIDEO",
    generate_transcript: false,
    download_quality: "BEST",
    audio_quality: "BEST",
  }) as SubscriptionType

const renderTable = (subscriptions: SubscriptionType[]) =>
  render(
    <SubscriptionsTable
      subscriptions={subscriptions}
      loading={false}
      fetchSubscriptions={vi.fn()}
      setSubscriptions={vi.fn()}
    />,
  )

// RTL's automatic cleanup never registers with globals off — see .claude/rules/frontend.md.
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("SubscriptionsTable Check now", () => {
  it("is offered on the user's own subscription only", () => {
    renderTable([sub(1, 2)])
    expect(screen.queryAllByTitle(CHECK)).toHaveLength(0)

    cleanup()
    renderTable([sub(1, 1)])
    expect(screen.queryAllByTitle(CHECK).length).toBeGreaterThan(0)
  })

  it("starts the check and polls the job it returns", async () => {
    post.mockResolvedValue({ data: { task: "subscription-check-1" } })
    renderTable([sub(1, 1)])

    fireEvent.click(screen.getAllByTitle(CHECK)[0])

    await waitFor(() => expect(startJobPolling).toHaveBeenCalledTimes(1))
    expect(post).toHaveBeenCalledWith(expect.stringMatching(/\/subscriptions\/1\/check$/))
    expect(startJobPolling.mock.calls[0][1]).toBe("subscription-check-1")
  })

  it("reports a check that could not be started", async () => {
    post.mockRejectedValue(new Error("network"))
    renderTable([sub(1, 1)])

    fireEvent.click(screen.getAllByTitle(CHECK)[0])

    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1))
    expect(startJobPolling).not.toHaveBeenCalled()
  })
})
