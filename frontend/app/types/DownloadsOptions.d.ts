export interface DownloadOptionsType {
  download_playlist: boolean
  audio_only: boolean
  transcript_only: boolean
  overwrite: boolean
  media_type: number
  url: string
  generate_transcript: boolean
  download_quality: string
  audio_quality: string
}

export type DownloadOptionsProps = {
  options: DownloadOptionsType
  setOptions: (downloadOptions: DownloadOptionsType) => void
}

export type TagInfo = {
  id: number
  name: string
}

export type Download = {
  media_details_id: number
  title: string
  channel: string
  media_type: string
  url: string
  file_path?: string
  file_size_bytes?: number
  transcript_task_progress?: number
  transcript_task_status?: string
  created_at?: string
  downloaded_at?: string
  release_timestamp?: string
  duration?: number
  thumbnail_path?: string
  playback_position?: number
  last_accessed?: string
  access_count?: number
  status?: string
  transcript_block_count?: number
  owner_id?: number
  rating?: number | null
  tags?: TagInfo[]
}

export type { SortDirection } from "@/app/_hooks/useTriStateSort"

// Grouping ("group by" folder view) on the Downloads grid
export type GroupDim = "channel" | "tag" | "downloaded" | "released"

export type MediaGroup = {
  key: string
  label: string
  count: number
  total_duration: number
  total_size_bytes: number
  min_date: string | null
  max_date: string | null
  video_count: number
  audio_count: number
  transcript_count: number
  sample_media_ids: number[]
}

// Filter applied when drilling into a group folder's media (leaf view).
export type GroupLeafFilter = {
  channel?: string
  untagged?: boolean
  dateField?: "downloaded" | "released"
  year?: number
  month?: number
}

export type MediaStats = {
  total_downloads: number
  total_transcript_blocks: number
  downloads_with_transcripts: number
}
