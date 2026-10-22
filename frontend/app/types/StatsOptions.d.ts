export interface LibraryOverview {
  total_media: number
  audio_count: number
  video_count: number
  total_duration_seconds: number
  unique_channels: number
  active_subscriptions: number | null
  transcripts_count: number
  total_disk_bytes: number
}

export interface StorageByType {
  media_type: string
  size_bytes: number
}

export interface StorageByChannel {
  channel: string
  size_bytes: number
}

export interface LargestFile {
  id: number
  title: string
  channel: string
  media_type: string
  size_bytes: number
}

export interface StorageStats {
  total_bytes: number
  by_type: StorageByType[]
  by_channel: StorageByChannel[]
  largest_files: LargestFile[]
}

export type Granularity = "day" | "week" | "month"

export interface PeriodDownload {
  period: string
  audio: number
  video: number
}

export interface CumulativeDownload {
  period: string
  total: number
  audio: number
  video: number
}

export interface DownloadsOverTime {
  granularity: Granularity
  periods: PeriodDownload[]
  cumulative: CumulativeDownload[]
  by_channel: Record<string, string | number>[]
  top_channels: string[]
}

export interface TranscriptionStats {
  total_media: number
  with_transcripts: number
  coverage_percent: number
  total_blocks: number
}

export interface TranscriptStorageByChannel {
  channel: string
  total_bytes: number
  text_bytes: number
  embedding_bytes: number
  transcript_count: number
  block_count: number
}

// On-disk bytes, indexes included; channels add up to total_bytes.
export interface TranscriptStorage {
  total_bytes: number
  text_bytes: number
  embedding_bytes: number
  // Whole-database size, sent only for the admin all-users view.
  database_bytes: number | null
  by_channel: TranscriptStorageByChannel[]
}

export interface MostReplayed {
  id: number
  title: string
  channel: string
  media_type: string
  access_count: number
  duration: number | null
}

export interface TopChannel {
  channel: string
  total_plays: number
}

export interface EngagementStats {
  most_replayed: MostReplayed[]
  top_channels: TopChannel[]
}

export interface ClippedSource {
  title: string
  channel: string
  clip_count: number
}

export interface ClipsPeriod {
  period: string
  count: number
}

export interface ClipsStats {
  total_clips: number
  complete_clips: number
  most_clipped_sources: ClippedSource[]
  over_time: ClipsPeriod[]
  granularity: Granularity
}

export interface SuccessRatePeriod {
  period: string
  success: number
  failed: number
  retry: number
}

export interface DownloadSuccessRate {
  granularity: Granularity
  periods: SuccessRatePeriod[]
  totals: { success: number; failed: number; retry: number; total: number }
  success_rate: number
}

export interface HeatmapDay {
  date: string
  count: number
}

export interface DownloadActivityHeatmap {
  data: HeatmapDay[]
  max_count: number
  total_days_active: number
  start_date: string
  end_date: string
}

// --- Stats filter types ---

export interface StatsFilterChannel {
  name: string
  media_count: number
}

export interface StatsFilterPlaylist {
  id: number
  name: string
  media_count: number
}

export interface StatsFilterOptions {
  channels: StatsFilterChannel[]
  playlists: StatsFilterPlaylist[]
}

export type StatsFilter =
  | { type: "channel"; channel: string }
  | { type: "playlist"; playlist_id: number; playlist_name: string }
  | null
