"use client"

import { useState } from "react"
import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import { formatRelativeTime, getFullTimestamp } from "@/app/utils"
import {
  CheckIcon,
  ClockIcon,
  Cog6ToothIcon,
  ExclamationTriangleIcon,
  ArrowPathIcon,
  XMarkIcon,
  NoSymbolIcon,
  MagnifyingGlassIcon,
  MoonIcon,
  DocumentTextIcon,
} from "@heroicons/react/20/solid"
import {
  ExclamationTriangleIcon as ExclamationTriangleOutlineIcon,
  ArrowPathIcon as ArrowPathOutlineIcon,
} from "@heroicons/react/24/outline"
import { DataTable } from "./data/DataTable"
import type { Column } from "./data/DataTable"
import { ConfirmDialog, ConfirmDetailGrid } from "./ConfirmDialog"
import { TaskErrorDetails } from "./TaskErrorDetails"
import { QueuedDownloadActions } from "./QueuedDownloadActions"
import { TaskRecord, SortDirection } from "@/app/types/TasksOptions"
import {
  canCancel,
  isRetryable,
  retryAttemptsLabel,
  retryNextTryLabel,
  sleepRemaining,
  taskRowMessage,
} from "@/app/lib/taskStatus"
import { useCountdownTick } from "@/app/_hooks/useCountdownTick"
import axios from "axios"
import toast from "react-hot-toast"
import { apiUrl, errorMessage } from "@/app/lib/api"

type TasksTableProps = {
  tableRows: TaskRecord[]
  loading: boolean
  refreshTasks: () => void
  sortBy: string | null
  sortDirection: SortDirection
  onSort: (column: string) => void
  selectedIds: Set<number>
  onSelectionChange: (ids: Set<number>) => void
  allSelected: boolean
  onSelectAll: (selected: boolean) => void
}

/** Sort fields offered on mobile, where column headers aren't available. */
const TASK_SORT_OPTIONS = [{ key: "created_at", label: "Created" }]

const TASK_TYPE_BADGE: Record<string, string> = {
  DOWNLOAD: "DL",
  TRANSCRIPT_GENERATION: "TR",
  CLIP_GENERATION: "CLIP",
  MEDIA_CONVERSION: "CV",
  SPRITE_GENERATION: "SPR",
}

/** The task type, marked when it belongs to a transcript-only item's chain. */
const TaskTypeBadge = ({ task, className }: { task: TaskRecord; className: string }) => (
  <Badge
    variant="outline"
    className={`font-mono gap-1 ${className}`}
    title={task.media_type === "TRANSCRIPT" ? "Transcript only: no media is kept" : undefined}
  >
    {TASK_TYPE_BADGE[task.task_type ?? ""] ?? task.task_type}
    {task.media_type === "TRANSCRIPT" && <DocumentTextIcon className="h-3 w-3" />}
  </Badge>
)

// `sleeping` is a display state layered over IN_PROGRESS, not a TaskStatus: the row really
// is running, it just hasn't started transferring yet (tasks/downloads.py _rate_limit_sleep).
const StatusBadge = ({ status, sleeping }: { status: string; sleeping?: boolean }) => {
  if (sleeping) {
    return (
      <Badge variant="queued" className="gap-1">
        <MoonIcon className="h-3 w-3" />
        Sleeping
      </Badge>
    )
  }

  switch (status) {
    case "COMPLETE":
      return (
        <Badge variant="success" className="gap-1">
          <CheckIcon className="h-3 w-3" />
          Complete
        </Badge>
      )
    case "RESOLVING":
      return (
        <Badge variant="info" className="gap-1">
          <MagnifyingGlassIcon className="h-3 w-3 animate-pulse" />
          Resolving
        </Badge>
      )
    case "QUEUED":
      return (
        <Badge variant="queued" className="gap-1">
          <ClockIcon className="h-3 w-3" />
          Queued
        </Badge>
      )
    case "IN_PROGRESS":
    case "POSTPROCESSING":
      return (
        <Badge variant="info" className="gap-1">
          <Cog6ToothIcon className="h-3 w-3 animate-spin" />
          Processing
        </Badge>
      )
    case "FAILED":
    case "UPSTREAM_FAILED":
      return (
        <Badge variant="error" className="gap-1">
          <XMarkIcon className="h-3 w-3" />
          Failed
        </Badge>
      )
    case "RETRY":
      return (
        <Badge variant="warning" className="gap-1">
          <ArrowPathIcon className="h-3 w-3" />
          Retry
        </Badge>
      )
    case "NOT_READY":
      return (
        <Badge variant="warning" className="gap-1">
          <ExclamationTriangleIcon className="h-3 w-3" />
          Not Released
        </Badge>
      )
    case "CANCELLED":
      return (
        <Badge variant="secondary" className="gap-1">
          <NoSymbolIcon className="h-3 w-3" />
          Cancelled
        </Badge>
      )
    case "DELETED":
      return (
        <Badge variant="secondary" className="gap-1">
          <XMarkIcon className="h-3 w-3" />
          Deleted
        </Badge>
      )
    default:
      return <Badge variant="secondary">{status}</Badge>
  }
}

// A download that failed because the video was previously deleted requires overwrite to
// re-download. Keyed off the "previously deleted" phrase in the backend status_message
// (DELETED_RETRY_MESSAGE in ytdl_router.py) so the retry dialog can pre-check Overwrite.
const isPreviouslyDeletedFailure = (task: TaskRecord) =>
  task.task_type === "DOWNLOAD" &&
  (task.status_message?.toLowerCase().includes("previously deleted") ?? false)

const getProgressGradientColor = (status: string, mediaType?: string, taskType?: string) => {
  if (status === "COMPLETE") return "color-mix(in srgb, var(--status-success) 60%, transparent)"
  if (status === "CANCELLED") return "color-mix(in srgb, var(--status-warning) 70%, transparent)"
  if (status === "FAILED" || status === "UPSTREAM_FAILED") return "color-mix(in srgb, var(--status-error) 70%, transparent)"
  if (status === "RETRY") return "color-mix(in srgb, var(--status-warning) 70%, transparent)"
  if (status === "NOT_READY") return "color-mix(in srgb, var(--status-warning) 70%, transparent)"
  if (taskType === "DOWNLOAD" && mediaType === "AUDIO") return "color-mix(in srgb, var(--status-warning) 70%, transparent)"
  return "color-mix(in srgb, var(--status-info) 70%, transparent)"
}

const getRowBackgroundStyle = (
  progressPercentage: number,
  status: string,
  isMultiStream: boolean,
  videoIsComplete: boolean,
  isTerminalState: boolean,
  mediaType?: string,
  taskType?: string
): React.CSSProperties | undefined => {
  if (progressPercentage <= 0) return undefined

  const baseStyle: React.CSSProperties = {
    backgroundRepeat: 'no-repeat',
  }

  if (isMultiStream && videoIsComplete) {
    // Video complete (shows as full-width base), audio progress overlaid
    const audioColor = isTerminalState ? getProgressGradientColor(status, mediaType, taskType) : "color-mix(in srgb, var(--status-warning) 50%, transparent)"
    const progressGradient = `linear-gradient(to right, ${audioColor} ${progressPercentage}%, color-mix(in srgb, var(--status-info) 50%, transparent) ${progressPercentage}%)`

    return {
      ...baseStyle,
      backgroundImage: progressGradient,
      backgroundSize: '100% 2px',
      backgroundPosition: 'bottom',
    }
  }

  const progressGradient = `linear-gradient(to right, ${getProgressGradientColor(status, mediaType, taskType)} ${progressPercentage}%, transparent ${progressPercentage}%)`

  return {
    ...baseStyle,
    backgroundImage: progressGradient,
    backgroundSize: '100% 2px',
    backgroundPosition: 'bottom',
  }
}

export function TasksTable({
  tableRows,
  loading,
  refreshTasks,
  sortBy,
  sortDirection,
  onSort,
  selectedIds,
  onSelectionChange,
  allSelected,
  onSelectAll,
}: TasksTableProps) {
  const [showCancelConfirmation, setShowCancelConfirmation] = useState(false)
  const [showRetryConfirmation, setShowRetryConfirmation] = useState(false)
  const [showQueuedActions, setShowQueuedActions] = useState(false)
  const [focusTask, setFocusTask] = useState<TaskRecord | null>(null)
  const [actionLoading, setActionLoading] = useState<"cancel" | "retry" | "prioritize" | null>(null)
  const [retryDownstream, setRetryDownstream] = useState(true)
  const [overwrite, setOverwrite] = useState(false)

  // Drives the "Retries in 4m" and "Starts in 2m" countdowns. Only runs while one is on
  // screen; the returned tick is a re-render signal, the deadlines come from the rows.
  const hasLiveCountdown =
    tableRows.some(
      (task) => (task.status === "RETRY" && task.next_retry_at) || sleepRemaining(task)
    ) || (focusTask?.status === "RETRY" && !!focusTask.next_retry_at)
  useCountdownTick(hasLiveCountdown)

  const handleCancelTask = async (task_id: string) => {
    setActionLoading("cancel")
    try {
      const response = await axios.delete(apiUrl(`/tasks/${task_id}`))
      if (response.status === 200) {
        const downstreamCount = response.data.downstream_tasks_cancelled
        if (downstreamCount > 0) {
          toast.success(
            `Cancelled task and ${downstreamCount} downstream task(s)`
          )
        } else {
          toast.success("Cancelled task")
        }
        setShowCancelConfirmation(false)
        setFocusTask(null)
        refreshTasks()
      }
    } catch (error) {
      toast.error("Failed to cancel task")
    } finally {
      setActionLoading(null)
    }
  }

  const handleRetryTask = async (retryDownstream: boolean, overwrite: boolean) => {
    if (!focusTask) return
    setActionLoading("retry")
    try {
      const response = await axios.post(
        apiUrl(`/tasks/${focusTask.id}/retry`),
        {
          retry_downstream: retryDownstream,
          overwrite: overwrite
        }
      )

      if (response.status === 200) {
        const count = response.data.retried_count
        const retryType = overwrite ? 'hard retried' : 'retried'
        toast.success(`${retryType.charAt(0).toUpperCase() + retryType.slice(1)} ${count} task(s)`)
        setShowRetryConfirmation(false)
        setFocusTask(null)
        refreshTasks()
      }
    } catch (error) {
      toast.error(
        `Failed to retry task: ${errorMessage(error, "Unknown error")}`
      )
    } finally {
      setActionLoading(null)
    }
  }

  const handlePrioritizeTask = async () => {
    if (!focusTask) return
    setActionLoading("prioritize")
    try {
      const response = await axios.post(
        apiUrl(`/tasks/${focusTask.id}/prioritize`)
      )
      if (response.status === 200) {
        toast.success("Task prioritized - it will execute next")
        setShowQueuedActions(false)
        setFocusTask(null)
        refreshTasks()
      }
    } catch (error) {
      toast.error(
        `Failed to prioritize task: ${errorMessage(error, "Unknown error")}`
      )
    } finally {
      setActionLoading(null)
    }
  }

  const handleCancelFromQueuedActions = async () => {
    if (!focusTask) return
    setActionLoading("cancel")
    try {
      const response = await axios.delete(apiUrl(`/tasks/${focusTask.task_id}`))
      if (response.status === 200) {
        const downstreamCount = response.data.downstream_tasks_cancelled
        if (downstreamCount > 0) {
          toast.success(
            `Cancelled task and ${downstreamCount} downstream task(s)`
          )
        } else {
          toast.success("Cancelled task")
        }
        setShowQueuedActions(false)
        setFocusTask(null)
        refreshTasks()
      }
    } catch (error) {
      toast.error("Failed to cancel task")
    } finally {
      setActionLoading(null)
    }
  }

  const handleRowClick = (task: TaskRecord) => {
    const isQueuedDownload = task.status === "QUEUED" && task.task_type === "DOWNLOAD"

    if (isQueuedDownload) {
      setFocusTask(task)
      setShowQueuedActions(true)
    } else if (canCancel(task.status)) {
      setFocusTask(task)
      setShowCancelConfirmation(true)
    } else if (isRetryable(task.status)) {
      setFocusTask(task)
      setRetryDownstream(true)
      setOverwrite(isPreviouslyDeletedFailure(task))
      setShowRetryConfirmation(true)
    } else {
      toast("No actions available for this task")
    }
  }

  const columns: Column<TaskRecord>[] = [
    {
      key: "status",
      label: "Status",
      mobile: "badge",
      render: (task) => <StatusBadge status={task.status} sleeping={!!sleepRemaining(task)} />,
    },
    {
      key: "task_type",
      label: "Type",
      mobile: "badge",
      renderMobile: (task) => <TaskTypeBadge task={task} className="text-[10px]" />,
      render: (task) => <TaskTypeBadge task={task} className="text-xs" />,
    },
    {
      key: "queue_position",
      label: "#",
      breakpoint: "sm",
      mobile: "meta",
      mobileOrder: 1,
      renderMobile: (task) =>
        task.queue_position === 0
          ? "Next"
          : task.queue_position != null && task.queue_position > 0
          ? String(task.queue_position)
          : null,
      render: (task) => (
        <span className="text-xs md:text-sm font-mono text-text-muted">
          {task.queue_position === 0
            ? "Next"
            : task.queue_position != null && task.queue_position > 0
            ? task.queue_position
            : "-"}
        </span>
      ),
    },
    {
      key: "status_message",
      label: "Message",
      breakpoint: "md",
      mobile: "meta",
      mobileOrder: 2,
      renderMobile: (task) => taskRowMessage(task) ?? task.status_message ?? null,
      render: (task) => {
        // A retrying or sleeping row leads with its countdown; the status_message it
        // replaces is still one hover (or one click) away.
        const liveMessage = taskRowMessage(task)
        return (
          <span
            className="text-xs md:text-sm text-text-secondary truncate block max-w-[300px]"
            title={task.status_message || ""}
          >
            {liveMessage ??
              (task.status_message && task.status_message.length > 60
                ? task.status_message.slice(0, 60) + "..."
                : task.status_message || "-")}
          </span>
        )
      },
    },
    {
      key: "title",
      label: "Title",
      mobile: "title",
      renderMobile: (task) => task.title || "-",
      tdClassName: "max-w-[200px] md:max-w-[400px]",
      render: (task) => (
        <span
          className="text-xs md:text-sm text-text-primary truncate block"
          title={task.title || ""}
        >
          {task.title && task.title.length > 70
            ? task.title.slice(0, 70) + "..."
            : task.title || "-"}
        </span>
      ),
    },
    {
      key: "channel",
      label: "Channel",
      breakpoint: "lg",
      mobile: "meta",
      mobileOrder: 3,
      renderMobile: (task) => task.channel || null,
      render: (task) => (
        <span className="text-xs md:text-sm text-text-secondary">
          {task.channel || "-"}
        </span>
      ),
    },
    {
      key: "created_at",
      label: "Created",
      sortable: true,
      mobile: "hidden",
      render: (task) => (
        <span
          className="text-xs md:text-sm text-text-muted font-mono cursor-help"
          title={getFullTimestamp(task.created_at)}
        >
          {formatRelativeTime(task.created_at)}
        </span>
      ),
    },
  ]

  return (
    <>
      <DataTable
        columns={columns}
        rows={tableRows}
        loading={loading}
        emptyMessage="No tasks found"
        getRowKey={(task) => task.id}
        onRowClick={handleRowClick}
        rowStyle={(task) =>
          getRowBackgroundStyle(
            Math.min(task.percent_complete || 0, 100),
            task.status,
            task.media_type === "VIDEO" && task.download_phase !== null,
            task.download_phase === "AUDIO",
            ["COMPLETE", "CANCELLED", "FAILED", "UPSTREAM_FAILED"].includes(task.status),
            task.media_type ?? undefined,
            task.task_type ?? undefined
          )
        }
        sortBy={sortBy}
        sortDirection={sortDirection}
        onSort={onSort}
        sortOptions={TASK_SORT_OPTIONS}
        mobileMeta={(task) => formatRelativeTime(task.created_at)}
        selection={{
          selectedIds,
          onSelectionChange,
          allSelected,
          onSelectAll,
          idOf: (task) => task.id,
        }}
      />

      {showQueuedActions && focusTask && (
        <QueuedDownloadActions
          open={showQueuedActions}
          handleOpen={() => setShowQueuedActions(!showQueuedActions)}
          task={focusTask}
          onPrioritize={handlePrioritizeTask}
          onCancel={handleCancelFromQueuedActions}
          onDismiss={() => {
            setShowQueuedActions(false)
            setFocusTask(null)
          }}
          isLoading={actionLoading === "prioritize" ? "prioritize" : actionLoading === "cancel" ? "cancel" : null}
        />
      )}

      {showCancelConfirmation && focusTask && (
        <ConfirmDialog
          open={showCancelConfirmation}
          onOpenChange={setShowCancelConfirmation}
          icon={<ExclamationTriangleOutlineIcon className="h-5 w-5 text-status-warning" />}
          title={focusTask.status === "NOT_READY" ? "Dismiss Task?" : "Cancel Task?"}
          description={
            focusTask.status === "NOT_READY"
              ? "This video has not been released yet. Dismissing removes it from the task list."
              : "This will cancel the task and mark all downstream tasks as failed."
          }
          descriptionClassName="text-status-warning"
          contentClassName="sm:max-w-lg"
          cancelLabel={focusTask.status === "NOT_READY" ? "No, Keep Task" : "No, Keep Running"}
          confirmLabel={focusTask.status === "NOT_READY" ? "Yes, Dismiss Task" : "Yes, Cancel Task"}
          loadingLabel={focusTask.status === "NOT_READY" ? "Dismissing..." : "Cancelling..."}
          isLoading={actionLoading === "cancel"}
          onConfirm={() => handleCancelTask(focusTask.task_id)}
          onCancel={() => {
            setShowCancelConfirmation(false)
            setFocusTask(null)
          }}
        >
          <div className="space-y-3 py-4">
            <ConfirmDetailGrid
              rows={[
                { label: "Type:", value: focusTask.task_type },
                {
                  label: "Title:",
                  value: focusTask.title || "N/A",
                  valueClassName: "text-text-primary",
                },
                { label: "Channel:", value: focusTask.channel || "N/A" },
                { label: "Status:", value: focusTask.status },
                ...(focusTask.status === "RETRY"
                  ? [
                      { label: "Attempts:", value: retryAttemptsLabel(focusTask) },
                      { label: "Next try:", value: retryNextTryLabel(focusTask) },
                    ]
                  : []),
                {
                  label: "Progress:",
                  value: `${focusTask.percent_complete}%`,
                  valueClassName: "text-matrix font-mono",
                },
              ]}
            />
            <TaskErrorDetails
              url={focusTask.download_job_url}
              message={focusTask.status_message}
              label="Message"
            />
          </div>
        </ConfirmDialog>
      )}

      {showRetryConfirmation && focusTask && (
        <ConfirmDialog
          open={showRetryConfirmation}
          onOpenChange={setShowRetryConfirmation}
          icon={<ArrowPathOutlineIcon className="h-5 w-5 text-matrix" />}
          title="Retry Task?"
          description="This will create a new task to retry the failed operation."
          descriptionClassName="text-matrix/80"
          contentClassName="sm:max-w-lg"
          confirmLabel="Yes, Retry Task"
          loadingLabel="Retrying..."
          confirmVariant="matrix"
          isLoading={actionLoading === "retry"}
          onConfirm={() => handleRetryTask(retryDownstream, overwrite)}
          onCancel={() => {
            setShowRetryConfirmation(false)
            setFocusTask(null)
          }}
        >
          <div className="space-y-4 py-4">
            <ConfirmDetailGrid
              rows={[
                { label: "Type:", value: focusTask.task_type },
                {
                  label: "Title:",
                  value: focusTask.title || "N/A",
                  valueClassName: "text-text-primary",
                },
                { label: "Channel:", value: focusTask.channel || "N/A" },
                {
                  label: "Status:",
                  value: focusTask.status,
                  valueClassName: "text-status-error",
                },
              ]}
            />
            <TaskErrorDetails
              url={focusTask.download_job_url}
              message={focusTask.status_message}
              label="Error"
            />
            <div className="space-y-3 pt-2">
              {focusTask.has_downstream_tasks && (
                <div className="flex items-center gap-3">
                  <Checkbox
                    id="retry-downstream"
                    checked={retryDownstream}
                    onCheckedChange={(checked) => setRetryDownstream(checked === true)}
                  />
                  <label
                    htmlFor="retry-downstream"
                    className="text-sm text-text-secondary cursor-pointer"
                  >
                    Also retry downstream tasks that failed due to this task
                  </label>
                </div>
              )}

              {(focusTask.task_type === "DOWNLOAD" ||
                focusTask.task_type === "TRANSCRIPT_GENERATION") && (
                <div className="flex items-center gap-3">
                  <Checkbox
                    id="overwrite"
                    checked={overwrite}
                    onCheckedChange={(checked) => setOverwrite(checked === true)}
                  />
                  <label
                    htmlFor="overwrite"
                    className="text-sm text-status-warning cursor-pointer"
                  >
                    {focusTask.task_type === "DOWNLOAD"
                      ? "Hard retry: Force overwrite (start download from beginning)"
                      : "Force recompute: Delete cached transcript and re-run Whisper"}
                  </label>
                </div>
              )}
            </div>
          </div>
        </ConfirmDialog>
      )}
    </>
  )
}
