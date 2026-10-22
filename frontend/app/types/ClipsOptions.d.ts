export type Clip = {
  id: number
  media_details_id?: number
  title: string
  description?: string
  start_time: number
  end_time: number
  duration?: number
  file_path?: string
  media_type: string
  status: string
  created_at: string
  source_title?: string
  source_channel?: string
  /** Set by the clip list: the source item's URL, null once the source is gone. */
  source_url?: string | null
  /** Set by the clip list: whether the app holds the source's file to play it from. */
  source_playable?: boolean
}

export type ClipStats = {
  total_clips: number
  audio_clips: number
  video_clips: number
}

export type { SortDirection } from "@/app/_hooks/useTriStateSort"
