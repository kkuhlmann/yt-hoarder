"use client"

import { Dispatch, SetStateAction, useState } from "react"
import axios from "axios"
import toast from "react-hot-toast"
import {
  ArrowPathIcon,
  BoltIcon,
  CheckIcon,
  XMarkIcon,
  UserPlusIcon,
} from "@heroicons/react/20/solid"

import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { apiUrl, errorMessage } from "@/app/lib/api"
import { DataTable } from "./data/DataTable"
import type { Column } from "./data/DataTable"
import { ActionList } from "./data/ActionList"
import { CollectionOfflineButton } from "@/app/_components/CollectionOfflineButton"
import { subscriptionSource } from "@/app/lib/offlineSync"
import type { ActionDescriptor } from "./data/ActionList"
import { EditSubscriptionDialog } from "@/app/_components/EditSubscriptionDialog"
import { ShareDialog } from "./ShareDialog"
import { SubscriptionType } from "@/app/types/SubscriptionsOptions"
import { useAuth } from "@/app/context/AuthContext"
import { startJobPolling } from "@/app/jobStatusService"
import { qualityLabel } from "./downloadOptions"

type SubscriptionsTableProps = {
  subscriptions: SubscriptionType[]
  loading: boolean
  fetchSubscriptions: (
    search: string | null,
    page_number: number
  ) => Promise<any>
  setSubscriptions: Dispatch<SetStateAction<SubscriptionType[]>>
}

const isTranscriptOnly = (sub: SubscriptionType) => sub.media_type === "TRANSCRIPT"

const subscriptionQuality = (sub: SubscriptionType) =>
  isTranscriptOnly(sub)
    ? "--"
    : sub.audio_only
      ? qualityLabel(sub.audio_quality, true)
      : qualityLabel(sub.download_quality, false)

const BooleanIndicator = ({ value }: { value: boolean }) =>
  value ? (
    <CheckIcon className="h-4 w-4 text-matrix" />
  ) : (
    <XMarkIcon className="h-4 w-4 text-text-muted" />
  )

export function SubscriptionsTable({
  subscriptions,
  loading,
  fetchSubscriptions,
  setSubscriptions,
}: SubscriptionsTableProps) {
  const [focusItem, setFocusItem] = useState<SubscriptionType | null>(null)
  const [editItem, setEditItem] = useState<SubscriptionType | null>(null)
  const { user } = useAuth()

  // PUT /subscriptions/{id}, its check and the sharing endpoints are all owner-or-admin only.
  const canManage = (sub: SubscriptionType) =>
    sub.user_id === user?.id || !!user?.is_admin

  const patchRow = (id: number, patch: Partial<SubscriptionType>) =>
    setSubscriptions((rows) =>
      rows.map((row) => (row.id === id ? { ...row, ...patch } : row))
    )

  const toggleEnabled = async (sub: SubscriptionType, enabled: boolean) => {
    patchRow(sub.id, { enabled })
    try {
      await axios.put(apiUrl(`/subscriptions/${sub.id}`), { enabled })
    } catch (error) {
      patchRow(sub.id, { enabled: sub.enabled })
      toast.error(errorMessage(error, "Failed to update subscription"))
    }
  }

  // No in-flight state of its own: a repeat press gets the same task id back, and
  // startJobPolling ignores an id it is already polling.
  const checkNow = async (sub: SubscriptionType) => {
    const name = sub.channel || sub.url
    let task: string
    try {
      const response = await axios.post(apiUrl(`/subscriptions/${sub.id}/check`))
      task = response.data.task
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't start the check"))
      return
    }

    let loadingToast: string | undefined
    startJobPolling(
      "Subscription",
      task,
      () => toast.success(`Checked ${name}. Anything new is queued in Tasks.`),
      () => toast.error(`Checking ${name} failed`, { id: loadingToast }),
      (toastId) => {
        loadingToast = toastId
        toast.loading(`Checking ${name} for new videos…`, { id: toastId })
      },
    )
  }

  const actions: ActionDescriptor<SubscriptionType>[] = [
    {
      key: "offlineSync",
      title: "Keep offline",
      icon: ArrowPathIcon,
      // A transcript-only subscription keeps no media, so there is nothing to sync.
      render: (sub) =>
        isTranscriptOnly(sub) ? null : (
          <CollectionOfflineButton source={subscriptionSource(sub.id)} />
        ),
    },
    {
      key: "checkNow",
      title: "Check for new videos now",
      icon: BoltIcon,
      hidden: (sub) => !canManage(sub),
      onClick: checkNow,
      buttonClassName: "hover:bg-matrix/20",
      iconClassName: "text-text-muted hover:text-matrix",
    },
    {
      key: "share",
      title: "Share",
      icon: UserPlusIcon,
      onClick: (sub) => {
        if (!canManage(sub)) {
          toast.error("Only the owner can manage sharing")
          return
        }
        setFocusItem(sub)
      },
      buttonClassName: "hover:bg-matrix/20",
      iconClassName: "text-text-muted hover:text-matrix",
    },
  ]

  const columns: Column<SubscriptionType>[] = [
    {
      key: "enabled",
      label: "On",
      stopRowClick: true,
      mobile: "leading",
      thClassName: "text-center",
      tdClassName: "text-center",
      // stopRowClick only guards the desktop cell; the mobile card's leading
      // slot has no propagation guard of its own and the whole card opens the
      // edit dialog.
      render: (sub) => (
        <span onClick={(e) => e.stopPropagation()}>
          <Switch
            checked={sub.enabled}
            disabled={!canManage(sub)}
            aria-label={`${sub.enabled ? "Disable" : "Enable"} ${
              sub.channel || sub.url
            }`}
            title={
              canManage(sub)
                ? sub.enabled
                  ? "Enabled — included in scheduled checks"
                  : "Disabled — skipped by scheduled checks"
                : "Only the owner can enable or disable this subscription"
            }
            onCheckedChange={(next) => toggleEnabled(sub, next)}
          />
        </span>
      ),
    },
    {
      key: "channel",
      label: "Channel",
      mobile: "title",
      renderMobile: (sub) => sub.channel,
      render: (sub) => (
        <span className="text-xs md:text-sm text-text-primary font-medium">
          {sub.channel}
        </span>
      ),
    },
    {
      key: "url",
      label: "URL",
      breakpoint: "md",
      mobile: "meta",
      mobileOrder: 2,
      renderMobile: (sub) => sub.url,
      tdClassName: "max-w-[200px]",
      render: (sub) => (
        <span
          className="text-xs md:text-sm text-text-secondary truncate block"
          title={sub.url}
        >
          {sub.url}
        </span>
      ),
    },
    {
      key: "string_match",
      label: "Filter",
      breakpoint: "md",
      mobile: "hidden",
      render: (sub) => (
        <span className="text-xs md:text-sm text-text-muted font-mono">
          {sub.string_match || "--"}
        </span>
      ),
    },
    {
      key: "audio_only",
      label: "Audio",
      mobile: "hidden",
      render: (sub) => <BooleanIndicator value={sub.audio_only} />,
    },
    {
      key: "download_quality",
      label: "Quality",
      breakpoint: "md",
      mobile: "meta",
      mobileOrder: 1,
      renderMobile: (sub) => subscriptionQuality(sub),
      render: (sub) => (
        <span className="text-xs md:text-sm text-text-secondary font-mono">
          {subscriptionQuality(sub)}
        </span>
      ),
    },
    {
      key: "generate_transcript",
      label: "Transcript",
      mobile: "hidden",
      render: (sub) => <BooleanIndicator value={sub.generate_transcript} />,
    },
    {
      key: "media_type",
      label: "Type",
      breakpoint: "lg",
      mobile: "badge",
      renderMobile: (sub) => (
        <Badge variant="outline" className="text-[10px] font-mono">
          {sub.media_type}
        </Badge>
      ),
      render: (sub) => (
        <Badge variant="outline" className="text-xs font-mono">
          {sub.media_type}
        </Badge>
      ),
    },
    {
      key: "actions",
      label: "Actions",
      stopRowClick: true,
      mobile: "hidden",
      thClassName: "text-center",
      render: (sub) => (
        <div className="flex items-center justify-center gap-1 [&_svg]:h-4 [&_svg]:w-4">
          <ActionList actions={actions} row={sub} />
        </div>
      ),
    },
  ]

  return (
    <>
      <DataTable
        columns={columns}
        rows={subscriptions}
        loading={loading}
        emptyMessage="No subscriptions found"
        getRowKey={(sub) => sub.id}
        rowClassName={(sub) => (sub.enabled ? undefined : "opacity-60")}
        onRowClick={setEditItem}
        renderActions={(sub) => <ActionList actions={actions} row={sub} />}
      />

      {focusItem && (
        <ShareDialog
          open
          onOpenChange={(open) => {
            if (!open) setFocusItem(null)
          }}
          entityIds={[focusItem.id]}
          entityType="subscriptions"
          entityTitle={focusItem.channel || focusItem.url}
        />
      )}

      {editItem && (
        <EditSubscriptionDialog
          id={editItem.id}
          open={editItem !== null}
          onOpenChange={(open) => {
            if (!open) setEditItem(null)
          }}
          fetchSubscriptions={fetchSubscriptions}
          subscriptions={subscriptions}
          setSubscriptions={setSubscriptions}
        />
      )}
    </>
  )
}
