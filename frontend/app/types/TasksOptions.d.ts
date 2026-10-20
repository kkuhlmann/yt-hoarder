export type TaskType =
  | 'DOWNLOAD'
  | 'TRANSCRIPT_GENERATION'
  | 'MEDIA_CONVERSION'
  | 'CLIP_GENERATION'
  | 'SPRITE_GENERATION'

export type TaskStatus =
  | 'NONE'
  | 'RESOLVING'
  | 'QUEUED'
  | 'IN_PROGRESS'
  | 'POSTPROCESSING'
  | 'COMPLETE'
  | 'RETRY'
  | 'FAILED'
  | 'SKIPPED'
  | 'UPSTREAM_FAILED'
  | 'CANCELLED'
  | 'DELETED'
  | 'NOT_READY'

export type TaskRecord = {
  id: number
  task_id: string
  task_type: TaskType
  percent_complete: number
  eta_seconds: number | null
  status: TaskStatus
  status_message: string | null
  created_at: string
  title: string | null
  channel: string | null
  release_timestamp: string | null
  media_type: 'AUDIO' | 'VIDEO' | 'TRANSCRIPT' | null
  upstream_task_ids: string[] | null
  download_job_url: string | null
  has_downstream_tasks?: boolean
  download_phase: 'VIDEO' | 'AUDIO' | null
  queue_position: number | null
  retry_count: number
  next_retry_at: string | null
  /** Wake time of the pre-download rate-limit sleep; null when the task isn't sleeping. */
  sleep_until: string | null
  error_code: string | null
  /** Attempt ceiling for this task type; null when the type is never retried. */
  max_retries: number | null
}

export type TaskFilterState = {
  showInProgress: boolean
  showQueued: boolean
  showNotReleased: boolean
  showRecentlyCompleted: boolean
  showCancelled: boolean
  showFailed: boolean
  showRetry: boolean
}

export type { SortDirection } from "@/app/_hooks/useTriStateSort"

export type TaskStats = {
  queued_total: number
  queued_downloads: number
  queued_transcripts: number
  processing: number
  failed: number
  retry: number
  not_ready: number
  completed_24h: number
}

export type StatCategory = 'processing' | 'queued' | 'retry' | 'not_ready' | 'failed' | 'completed'
