import type { MediaChoice, MediaOptions, Outcome, TaskFeed } from "../api"
import {
  AUDIO_QUALITIES,
  durationSeconds,
  uploadedAfterTimestamp,
  VIDEO_QUALITIES,
  type AudioQuality,
  type VideoQuality,
} from "../choices"
import type { ExtensionRequest } from "../messages"
import type { Prefs, SectionPrefs } from "../settings"
import { classifyPage } from "../youtube"
import { startTaskList } from "./tasks"

export type PopupDeps = {
  activeTabUrl(): Promise<string | undefined>
  isConfigured(): Promise<boolean>
  loadPrefs(): Promise<Prefs>
  savePrefs(prefs: Prefs): Promise<void>
  send(message: ExtensionRequest): Promise<Outcome | undefined>
  listTasks(): Promise<TaskFeed | undefined>
  openOptions(): void
}

type Section = "download" | "subscribe"

export const NO_REPLY: Outcome = {
  tone: "error",
  message: "The extension's background script didn't answer. Try again.",
}

export const BAD_LENGTH_MESSAGE = "Enter lengths in minutes, for example 1.5."
export const LENGTHS_SWAPPED_MESSAGE = "The minimum length is longer than the maximum."

function byId<T extends HTMLElement = HTMLElement>(doc: Document, id: string): T {
  const element = doc.getElementById(id)
  if (!element) throw new Error(`Missing #${id}`)
  return element as T
}

function checkedValue(doc: Document, name: string): string | undefined {
  return doc.querySelector<HTMLInputElement>(`input[name="${name}"]:checked`)?.value
}

function check(doc: Document, name: string, value: string): void {
  const input = doc.querySelector<HTMLInputElement>(`input[name="${name}"][value="${value}"]`)
  if (input) input.checked = true
}

function fillSelect(
  doc: Document,
  select: HTMLSelectElement,
  choices: readonly { value: string; label: string }[],
  selected: string
): void {
  select.replaceChildren(
    ...choices.map(({ value, label }) => {
      const option = doc.createElement("option")
      option.value = value
      option.textContent = label
      return option
    })
  )
  select.value = selected
}

/**
 * One section's type radios, quality selects and checkboxes. Like the app's forms, each type shows
 * only what it uses: video quality for Video, audio quality for Audio, neither for Transcript only.
 */
function bindMediaOptions(doc: Document, section: Section, prefs: SectionPrefs): () => MediaOptions {
  const transcribe = byId<HTMLInputElement>(doc, `${section}-transcribe`)
  const videoQuality = byId<HTMLSelectElement>(doc, `${section}-video-quality`)
  const audioQuality = byId<HTMLSelectElement>(doc, `${section}-audio-quality`)

  fillSelect(doc, videoQuality, VIDEO_QUALITIES, prefs.videoQuality)
  fillSelect(doc, audioQuality, AUDIO_QUALITIES, prefs.audioQuality)
  check(doc, `${section}-mode`, prefs.mediaType)
  transcribe.checked = prefs.transcribe

  const mediaType = () => (checkedValue(doc, `${section}-mode`) ?? "VIDEO") as MediaChoice
  const sync = () => {
    const type = mediaType()
    byId(doc, `${section}-quality`).hidden = type === "TRANSCRIPT"
    byId(doc, `${section}-video-quality-row`).hidden = type !== "VIDEO"
    byId(doc, `${section}-audio-quality-row`).hidden = type !== "AUDIO"
    byId(doc, `${section}-transcribe-row`).hidden = type === "TRANSCRIPT"
  }
  doc
    .querySelectorAll<HTMLInputElement>(`input[name="${section}-mode"]`)
    .forEach((radio) => radio.addEventListener("change", sync))
  sync()

  return () => ({
    mediaType: mediaType(),
    transcribe: transcribe.checked,
    videoQuality: videoQuality.value as VideoQuality,
    audioQuality: audioQuality.value as AudioQuality,
  })
}

function remembered({ mediaType, transcribe, videoQuality, audioQuality }: MediaOptions): SectionPrefs {
  return { mediaType, transcribe, videoQuality, audioQuality }
}

// A number box holding something that isn't a number reports an empty value, which on its own
// would read as no filter at all; badInput is the only sign the user typed anything.
function lengthSeconds(field: HTMLInputElement): number | null | undefined {
  return field.validity.badInput ? undefined : durationSeconds(field.value)
}

export async function initPopup(doc: Document, deps: PopupDeps): Promise<void> {
  const result = byId(doc, "result")
  const show = (outcome: Outcome) => {
    result.textContent = outcome.message
    result.dataset.tone = outcome.tone
  }

  byId(doc, "open-settings").addEventListener("click", () => deps.openOptions())
  byId(doc, "setup-button").addEventListener("click", () => deps.openOptions())

  if (!(await deps.isConfigured())) {
    byId(doc, "setup").hidden = false
    return
  }

  const tasks = startTaskList(
    {
      section: byId(doc, "tasks"),
      status: byId(doc, "tasks-status"),
      list: byId(doc, "tasks-list"),
    },
    { listTasks: deps.listTasks, now: Date.now }
  )

  const targets = classifyPage((await deps.activeTabUrl()) ?? "")
  const prefs = await deps.loadPrefs()
  const subscribeUrl = targets.playlist ?? targets.channel

  async function submit(button: HTMLButtonElement, message: ExtensionRequest) {
    button.disabled = true
    show({ tone: "info", message: "Sending…" })
    try {
      show((await deps.send(message)) ?? NO_REPLY)
      tasks.refresh()
    } catch {
      show(NO_REPLY)
    } finally {
      button.disabled = false
    }
  }

  if (targets.video || targets.playlist) {
    byId(doc, "download").hidden = false
    const options = bindMediaOptions(doc, "download", prefs.download)
    const overwrite = byId<HTMLInputElement>(doc, "download-overwrite")
    const button = byId<HTMLButtonElement>(doc, "download-button")
    const offersBoth = Boolean(targets.video && targets.playlist)
    byId(doc, "download-scope").hidden = !offersBoth
    if (!targets.video) button.textContent = "Download playlist"

    button.addEventListener("click", () => {
      const wholePlaylist = offersBoth ? checkedValue(doc, "download-scope") === "playlist" : !targets.video
      const url = wholePlaylist ? targets.playlist : targets.video
      if (!url) return
      const chosen = options()
      prefs.download = remembered(chosen)
      void deps.savePrefs(prefs)
      void submit(button, {
        type: "download",
        request: { url, ...chosen, overwrite: overwrite.checked, wholePlaylist },
      })
    })
  }

  if (subscribeUrl) {
    byId(doc, "subscribe").hidden = false
    byId(doc, "subscribe-url").textContent = subscribeUrl
    const options = bindMediaOptions(doc, "subscribe", prefs.subscribe)
    const filter = byId<HTMLInputElement>(doc, "subscribe-filter")
    const after = byId<HTMLInputElement>(doc, "subscribe-after")
    const minLength = byId<HTMLInputElement>(doc, "subscribe-min")
    const maxLength = byId<HTMLInputElement>(doc, "subscribe-max")
    const button = byId<HTMLButtonElement>(doc, "subscribe-button")

    button.addEventListener("click", () => {
      const minDurationSeconds = lengthSeconds(minLength)
      const maxDurationSeconds = lengthSeconds(maxLength)
      if (minDurationSeconds === undefined || maxDurationSeconds === undefined) {
        byId<HTMLDetailsElement>(doc, "subscribe-filters").open = true
        show({ tone: "error", message: BAD_LENGTH_MESSAGE })
        return
      }
      if (
        minDurationSeconds !== null &&
        maxDurationSeconds !== null &&
        minDurationSeconds > maxDurationSeconds
      ) {
        byId<HTMLDetailsElement>(doc, "subscribe-filters").open = true
        show({ tone: "error", message: LENGTHS_SWAPPED_MESSAGE })
        return
      }
      const chosen = options()
      prefs.subscribe = remembered(chosen)
      void deps.savePrefs(prefs)
      void submit(button, {
        type: "subscribe",
        request: {
          url: subscribeUrl,
          ...chosen,
          titleFilter: filter.value,
          uploadedAfter: uploadedAfterTimestamp(after.value),
          minDurationSeconds,
          maxDurationSeconds,
        },
      })
    })
  } else if (targets.video) {
    byId(doc, "subscribe-hint").hidden = false
  }

  if (!targets.video && !subscribeUrl) byId(doc, "unsupported").hidden = false

  const offered = SECTIONS.filter((name) => !byId(doc, name).hidden)
  if (offered.length === 2) bindTabs(doc, prefs, deps)
}

const SECTIONS: readonly Section[] = ["download", "subscribe"]

/**
 * A page that offers both actions shows one at a time, so neither pushes the other out of the
 * popup's reach. Pages offering a single action keep the plain panel with no tab bar.
 */
function bindTabs(doc: Document, prefs: Prefs, deps: PopupDeps): void {
  byId(doc, "tabs").hidden = false

  const select = (active: Section, focus = false) => {
    for (const name of SECTIONS) {
      const tab = byId<HTMLButtonElement>(doc, `tab-${name}`)
      tab.setAttribute("aria-selected", String(name === active))
      tab.tabIndex = name === active ? 0 : -1
      byId(doc, name).hidden = name !== active
      if (focus && name === active) tab.focus()
    }
  }

  for (const name of SECTIONS) {
    byId(doc, `tab-${name}`).addEventListener("click", () => {
      select(name)
      prefs.lastTab = name
      void deps.savePrefs(prefs)
    })
    byId(doc, `tab-${name}`).addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return
      event.preventDefault()
      const other = SECTIONS[1 - SECTIONS.indexOf(name)]
      select(other, true)
      prefs.lastTab = other
      void deps.savePrefs(prefs)
    })
  }

  select(prefs.lastTab)
}
