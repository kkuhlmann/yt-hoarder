import { Dispatch, SetStateAction } from "react"

export type SubscriptionType = {
  id: number
  url: string
  enabled: boolean
  audio_only: boolean
  media_type: string
  string_match: string
  overwrite: boolean
  date_filter: Date
  min_duration_seconds?: number | null
  max_duration_seconds?: number | null
  channel?: string
  generate_transcript: boolean
  download_quality: string
  audio_quality: string
  user_id?: number
}

export type SubscriptionOptionsType = {
  url: string
  audio_only: boolean
  transcript_only: boolean
  media_type: string
  string_match?: string
  overwrite: boolean
  date_filter?: Date
  min_duration_seconds?: number | null
  max_duration_seconds?: number | null
  generate_transcript: boolean
  download_quality: string
  audio_quality: string
}

export type SubscriptionsProps = {
  options: SubscriptionOptions
  setOptions: (options: SubscriptionOptions) => void
  fetchSubscriptions: (
    search: string | null,
    pageNumber: number
  ) => Promise<any>
  setSubscriptions: Dispatch<SetStateAction<SubscriptionType[]>>
  setPageCount: Dispatch<SetStateAction<number>>
  pageNumber: number
}
