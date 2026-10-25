import { SettingsConfig } from "@/app/types/Settings"

export const SETTINGS_CONFIG: SettingsConfig = {
  download: [
    {
      key: "download_sleep_seconds",
      label: "Download Sleep",
      description: "Delay (seconds) between downloads to avoid rate limiting",
      type: "number",
      min: 0,
      max: 300,
    },
    {
      key: "download_rate_limit_kbps",
      label: "Speed Limit",
      description:
        "Max download speed in KB/s (0 = unlimited). Applies per download, so N parallel downloads use up to N × this",
      type: "number",
      min: 0,
    },
    {
      key: "request_sleep_seconds",
      label: "Request Sleep",
      description:
        "Delay (seconds) between yt-dlp's HTTP requests while reading metadata (0 = off). Slows channel enumeration",
      type: "number",
      min: 0,
      max: 60,
    },
    {
      key: "cleanup_age_hours",
      label: "Cleanup Age",
      description: "Delete temporary files older than this (hours)",
      type: "number",
      min: 1,
      max: 168,
    },
    {
      key: "player_client",
      label: "Player Client",
      description: "YouTube player clients for yt-dlp (fallback order)",
      type: "list",
      options: [
        { value: "tv_samsung", label: "tv_samsung" },
        { value: "visionos", label: "visionos" },
        { value: "tv", label: "tv" },
        { value: "web_embedded", label: "web_embedded" },
        { value: "web", label: "web" },
        { value: "web_safari", label: "web_safari" },
        { value: "tv_simply", label: "tv_simply" },
        { value: "ios", label: "ios" },
        { value: "mweb", label: "mweb" },
        { value: "web_music", label: "web_music" },
        { value: "web_creator", label: "web_creator" },
        { value: "android", label: "android" },
        { value: "android_vr", label: "android_vr" },
        { value: "tv_downgraded", label: "tv_downgraded" },
      ],
    },
  ],
  tasks: [
    {
      key: "subscription_check_minutes",
      label: "Subscription Check",
      description:
        "How often subscriptions are checked for new content (minutes).",
      type: "number",
      min: 1,
      max: 1440,
    },
    {
      key: "default_lane_concurrency",
      label: "Default Lane",
      description:
        "Orchestration jobs in parallel (metadata fetches, playlist expansion)",
      type: "number",
      min: 1,
      max: 8,
    },
    {
      key: "downloads_lane_concurrency",
      label: "Downloads Lane",
      description:
        "Downloads in parallel. Above 1, Download Sleep no longer paces the whole app — expect more rate limiting",
      type: "number",
      min: 1,
      max: 8,
    },
    {
      key: "subscriptions_lane_concurrency",
      label: "Subscriptions Lane",
      description: "Channel and playlist enumerations in parallel",
      type: "number",
      min: 1,
      max: 8,
    },
    {
      key: "ml_lane_concurrency",
      label: "ML Lane",
      description:
        "Transcription, sprite and clip jobs in parallel. Each transcription loads its own Whisper model, so raising this multiplies memory and CPU use",
      type: "number",
      min: 1,
      max: 8,
    },
  ],
  transcript: [
    {
      key: "transcript_chunk_duration",
      label: "Chunk Duration",
      description: "Audio chunk duration for transcription (seconds)",
      type: "number",
      min: 60,
      max: 1800,
    },
    {
      key: "transcript_block_duration",
      label: "Block Duration",
      description: "Transcript block grouping duration (seconds)",
      type: "number",
      min: 5,
      max: 120,
    },
    {
      key: "force_whisper_transcription",
      label: "Force Whisper",
      description:
        "Always use Whisper for transcription, ignoring available subtitles",
      type: "boolean",
    },
  ],
  display: [
    {
      key: "subscription_table_page_size",
      label: "Subscription Page Size",
      description: "Rows per page in subscriptions table",
      type: "number",
      min: 5,
      max: 100,
    },
    {
      key: "download_table_page_size",
      label: "Download Page Size",
      description: "Rows per page in downloads table",
      type: "number",
      min: 5,
      max: 100,
    },
  ],
}
