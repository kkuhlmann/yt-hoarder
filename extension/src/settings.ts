import type { MediaChoice } from "./api"
import { isAudioQuality, isVideoQuality, type AudioQuality, type VideoQuality } from "./choices"

/**
 * Everything the extension keeps, in `storage.local`. Never `storage.sync`: that would copy the
 * token into the user's browser account.
 */
export type Settings = {
  /** The address as the user entered it, normalized. */
  serverUrl: string
  /** Where the API answered under it; empty until a connection test succeeds. */
  apiBase: string
  token: string
}

/**
 * The choices a section starts from next time. Overwrite and the subscription filters are left
 * out on purpose: they belong to one video or channel, and a carried-over Overwrite would quietly
 * replace files on later downloads.
 */
export type SectionPrefs = {
  mediaType: MediaChoice
  transcribe: boolean
  videoQuality: VideoQuality
  audioQuality: AudioQuality
}
export type Tab = "download" | "subscribe"
export type Prefs = { download: SectionPrefs; subscribe: SectionPrefs; lastTab: Tab }

const DEFAULT_SECTION: SectionPrefs = {
  mediaType: "VIDEO",
  transcribe: false,
  videoQuality: "BEST",
  audioQuality: "BEST",
}

export const DEFAULT_PREFS: Prefs = {
  download: DEFAULT_SECTION,
  subscribe: DEFAULT_SECTION,
  lastTab: "download",
}

const MEDIA_CHOICES: readonly MediaChoice[] = ["VIDEO", "AUDIO", "TRANSCRIPT"]

type StorageArea = {
  get(keys: string[]): Promise<Record<string, unknown>>
  set(items: Record<string, unknown>): Promise<void>
}

function text(value: unknown): string {
  return typeof value === "string" ? value : ""
}

function sectionPrefs(value: unknown, fallback: SectionPrefs): SectionPrefs {
  if (typeof value !== "object" || value === null) return fallback
  const { mediaType, transcribe, videoQuality, audioQuality } = value as Record<string, unknown>
  return {
    mediaType: MEDIA_CHOICES.includes(mediaType as MediaChoice)
      ? (mediaType as MediaChoice)
      : fallback.mediaType,
    transcribe: typeof transcribe === "boolean" ? transcribe : fallback.transcribe,
    videoQuality: isVideoQuality(videoQuality) ? videoQuality : fallback.videoQuality,
    audioQuality: isAudioQuality(audioQuality) ? audioQuality : fallback.audioQuality,
  }
}

export function createSettingsStore(area: StorageArea) {
  async function load(): Promise<Settings> {
    const stored = await area.get(["serverUrl", "apiBase", "token"])
    return {
      serverUrl: text(stored.serverUrl),
      apiBase: text(stored.apiBase),
      token: text(stored.token),
    }
  }

  return {
    load,

    async save(update: Partial<Settings>): Promise<void> {
      await area.set(update)
    },

    async isConfigured(): Promise<boolean> {
      const { apiBase, token } = await load()
      return Boolean(apiBase && token)
    },

    async loadPrefs(): Promise<Prefs> {
      const { prefs } = await area.get(["prefs"])
      const stored = typeof prefs === "object" && prefs !== null ? (prefs as Record<string, unknown>) : {}
      return {
        download: sectionPrefs(stored.download, DEFAULT_PREFS.download),
        subscribe: sectionPrefs(stored.subscribe, DEFAULT_PREFS.subscribe),
        lastTab: stored.lastTab === "subscribe" ? "subscribe" : "download",
      }
    },

    async savePrefs(prefs: Prefs): Promise<void> {
      await area.set({ prefs })
    },
  }
}

export type SettingsStore = ReturnType<typeof createSettingsStore>
