"use client"

import {
  useCallback,
  useState,
  useRef,
  useEffect,
  type Dispatch,
  type SetStateAction,
} from "react"
import { DownloadsCard } from "@/app/_components/DownloadsCard"
import { useView } from "@/app/context/ViewContext"
import axios from "axios"
import { apiUrl, fetchPage, searchParam } from "@/app/lib/api"
import { mediaApi } from "@/app/lib/mediaApi"
import { scopeParams } from "@/app/lib/mediaScope"
import { groupFilterParams } from "@/app/lib/groupFilter"
import { parseDeepLink, stripDeepLink, type DeepLink } from "@/app/lib/deepLink"
import { fetchOfflineLibrary, fetchOfflineStats } from "@/app/lib/offlineLibrary"
import { useOffline } from "@/app/context/OfflineContext"
import { OfflineUnavailable } from "@/app/_components/OfflineUnavailable"
import { SubscriptionsCard } from "@/app/_components/SubscriptionsCard"
import { ClipsCard } from "@/app/_components/ClipsCard"
import { PlaylistsCard } from "@/app/_components/PlaylistsCard"
import { TasksCard } from "@/app/_components/TasksCard"
import { SettingsCard } from "@/app/_components/SettingsCard"
import { StatsCard } from "@/app/_components/StatsCard"
import { SubscriptionOptionsType } from "@/app/types/SubscriptionsOptions"
import {
  DEFAULT_SUBSCRIPTION,
  INITIAL_DOWNLOAD_OPTIONS,
} from "@/app/_components/downloadOptions"
import { GroupLeafFilter, DownloadOptionsType } from "@/app/types/DownloadsOptions"
import type { TaskRecord } from "@/app/types/TasksOptions"
import "@daypicker/react/style.css"
import { useMediaPlayer } from "./context/MediaPlayerContext"
import { useAdmin } from "./context/AdminContext"
import { AudioPlayer } from "@/app/_components/MediaPlayer"
import { AudioVisualizer, type VisualizerStyle } from "@/app/_components/AudioVisualizer"
import type { AudioAnalyserHandle } from "@/app/_hooks/useAudioAnalyser"
import { useStoredValue, writeStored } from "@/app/_hooks/useStoredValue"
import { StarRating } from "@/app/_components/StarRating"
import { Button } from "@/components/ui/button"
import { AnimatePresence, motion } from "framer-motion"
import {
  BackwardIcon,
  ForwardIcon,
  ArrowPathIcon,
  ArrowsRightLeftIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  DocumentTextIcon,
} from "@heroicons/react/20/solid"

const INITIAL_SUBSCRIPTION_OPTIONS = {
  audio_only: false,
  overwrite: false,
  transcript_only: false,
  media_type: "0",
  url: "",
  generate_transcript: false,
  download_quality: "BEST",
  audio_quality: "BEST",
}

function readVisualizerStyle(): VisualizerStyle | null {
  const stored = localStorage.getItem("visualizerMode")
  if (stored === "bars" || stored === "area") return stored
  // "off" is a stored choice, not an absent key. Without this it falls through
  // to the default below and the visualizer turns itself back on.
  if (stored === "off") return null
  return "bars"
}

export default function HomePage() {
  // The two add-forms below are scoped to the view that shows them: each holds
  // the view it was filled under, and reads back as empty anywhere else. See the
  // derivation under `view`.
  const [downloadForm, setDownloadForm] = useState({
    view: "downloads",
    value: INITIAL_DOWNLOAD_OPTIONS,
  })
  const [subForm, setSubForm] = useState({
    view: "subscriptions",
    value: INITIAL_SUBSCRIPTION_OPTIONS,
  })
  const [subscriptions, setSubscriptions] = useState([DEFAULT_SUBSCRIPTION])
  const [search, setSearch] = useState("")
  // Read once: the URL is cleaned straight away, and a handled link must not replay.
  const [deepLink, setDeepLink] = useState<DeepLink | null>(() =>
    typeof window === "undefined" ? null : parseDeepLink(window.location.search)
  )
  const clearDeepLink = useCallback(() => setDeepLink(null), [])
  useEffect(() => {
    // Keeps a refresh or a copied address bar from replaying the jump.
    const cleaned = stripDeepLink(window.location.href)
    const current = window.location.pathname + window.location.search + window.location.hash
    if (cleaned !== current) window.history.replaceState(window.history.state, "", cleaned)
  }, [])
  const [semanticSearch, setSemanticSearch] = useState("")
  const [semanticWeight, setSemanticWeight] = useState(1.0)
  const [subscriptionSearch, setSubscriptionSearch] = useState("")
  const [mediaStatus, setMediaStatus] = useState("COMPLETE")
  const { view, setView } = useView()
  const { adminMode, adminParam } = useAdmin()
  const { offlineMode } = useOffline()

  // Each add-form belongs to one view and reads as empty from anywhere else, so
  // no effect is needed to clear it on navigation.
  const downloadOptions =
    downloadForm.view === view ? downloadForm.value : INITIAL_DOWNLOAD_OPTIONS
  const setdownloadOptions = useCallback(
    (value: DownloadOptionsType) => setDownloadForm({ view, value }),
    [view]
  )
  const subOptions = subForm.view === view ? subForm.value : INITIAL_SUBSCRIPTION_OPTIONS
  const setSubOptions = useCallback<Dispatch<SetStateAction<SubscriptionOptionsType>>>(
    (action) =>
      setSubForm((prev) => {
        const base = prev.view === view ? prev.value : INITIAL_SUBSCRIPTION_OPTIONS
        return { view, value: typeof action === "function" ? action(base) : action }
      }),
    [view]
  )
  const {
    audioPlayer,
    closeAudioPlayer,
    playNext,
    playPrevious,
    toggleAutoplay,
    toggleShuffle,
    rateMedia,
    nextTrack,
  } = useMediaPlayer()

  // The bottom bar's <audio> and whether the audio page (DownloadsCard) is open: both
  // live here because the bar, which sits outside every view, can open that page.
  const [audioElement, setAudioElement] = useState<HTMLAudioElement | null>(null)
  const [audioPageOpen, setAudioPageOpen] = useState(false)
  const openAudioPage = () => {
    setView("downloads")
    setAudioPageOpen(true)
  }
  const closeAudio = () => {
    closeAudioPlayer()
    setAudioPageOpen(false)
  }

  // Queue navigation state, shared by the footer buttons and the OS lock-screen
  // controls so the two can't disagree about what's available.
  const canGoPrevious =
    audioPlayer.currentIndex !== undefined && audioPlayer.currentIndex > 0
  const mediaSessionQueue = audioPlayer.playlistId
    ? {
        hasNext: !!nextTrack,
        canGoPrevious,
        onNextTrack: playNext,
        onPreviousTrack: playPrevious,
      }
    : undefined

  // Audio visualizer style: starts on bars, cycled bars -> mountain -> off and
  // persisted in localStorage (like the theme). The shared analyser handle is
  // populated by AudioPlayer and read by the backdrop.
  const visualizerStyle = useStoredValue(readVisualizerStyle, "bars")
  const [infoExpanded, setInfoExpanded] = useState(false)
  const analyserHandleRef = useRef<AudioAnalyserHandle | null>(null)

  const cycleVisualizer = useCallback(() => {
    const cycle: (VisualizerStyle | null)[] = [null, "bars", "area"]
    const next = cycle[(cycle.indexOf(readVisualizerStyle()) + 1) % cycle.length]
    writeStored("visualizerMode", next ?? "off")
  }, [])

  useEffect(() => {
    if (view === "settings" && !adminMode) {
      setView("downloads")
    }
  }, [view, adminMode, setView])

  const debounceTimeout = useRef<NodeJS.Timeout | null>(null)

  const fetchData = useCallback(
    async (
      endpoint: string,
      search: string | null,
      status: string | null,
      pageNumber: number,
      errorMessage: string,
    ) => {
      return fetchPage(`/${endpoint}`, {
        search: searchParam(search),
        status: status,
        page: pageNumber,
        ...adminParam,
      }).catch((error) => {
        console.error(errorMessage, error)
      })
    },
    [adminParam],
  )

  const fetchTranscriptSegments = useCallback(
    (
      standard_search: string,
      semantic_search: string,
      semanticWeight: number,
      filters: {
        tagIds: number[]
        minRating: number | null
        groupFilter: GroupLeafFilter | null
      },
    ) => {
      if (debounceTimeout.current) {
        clearTimeout(debounceTimeout.current)
      }
      return new Promise((resolve, reject) => {
        debounceTimeout.current = setTimeout(() => {
          const params: Record<string, string | number | boolean | undefined> = {
            semantic_search,
            semantic_weight: semanticWeight,
            ...adminParam,
            ...groupFilterParams(filters.groupFilter),
          }
          const standard = searchParam(standard_search)
          if (standard) {
            params.standard_search = standard
          }
          if (filters.tagIds.length > 0) {
            params.tag_ids = filters.tagIds.join(",")
          }
          if (filters.minRating != null) {
            params.min_rating = filters.minRating
          }
          axios
            .get(apiUrl(mediaApi.semanticSearch), { params })
            .then((response) => {
              resolve(response.data)
            })
            .catch((error) => {
              reject(error)
            })
        }, 1000)
      })
    },
    [adminParam],
  )

  const fetchDownloadJobs = useCallback(
    (
      search: string | null,
      status: string | null,
      pageNumber: number,
      sortBy?: string | null,
      sortDirection?: string | null,
      tagIds?: number[] | null,
      minRating?: number | null,
      groupFilter?: GroupLeafFilter | null,
      pageSize?: number,
    ) => {
      // Offline mode reads the downloaded records instead. Swapping the fetcher
      // here rather than inside DownloadsCard keeps the swap to one place: this is
      // the only path by which the library list is loaded.
      if (offlineMode) {
        return fetchOfflineLibrary({
          search,
          pageNumber,
          sortBy,
          sortDirection,
          tagIds,
          minRating,
          pageSize,
        })
      }

      const params: Record<string, any> = {
        search: searchParam(search),
        ...scopeParams(status),
        page: pageNumber,
        sort_by: sortBy,
        sort_direction: sortDirection,
        ...adminParam,
      }
      if (pageSize) {
        params.page_size = pageSize
      }
      if (tagIds && tagIds.length > 0) {
        params.tag_ids = tagIds.join(",")
      }
      if (minRating != null) {
        params.min_rating = minRating
      }
      Object.assign(params, groupFilterParams(groupFilter))
      return fetchPage(mediaApi.list, params).catch((error) => {
        console.error("Failed to fetch job data", error)
        return { pageCount: 0, tableRows: [] }
      })
    },
    [adminParam, offlineMode],
  )

  // useCallback like its siblings above/below: SubscriptionsCard now lists this
  // in a fetch dep array, so a fresh identity per render would refetch on every
  // render of this page.
  const fetchSubscriptions = useCallback(
    (search: string | null = "", pageNumber: number = 1) => {
      return fetchData(
        "subscriptions",
        search,
        null,
        pageNumber,
        "Failed to fetch subscription data",
      )
    },
    [fetchData],
  )

  const fetchTasks = useCallback(
    (
      search: string | null,
      statuses: string | null,
      sinceHours: number,
      pageNumber: number,
      sortBy?: string | null,
      sortDirection?: string | null,
    ) => {
      return fetchPage<TaskRecord>("/tasks", {
        search: searchParam(search),
        statuses: statuses,
        since_hours: sinceHours,
        page: pageNumber,
        sort_by: sortBy,
        sort_direction: sortDirection,
        ...adminParam,
      }).catch((error) => {
        console.error("Failed to fetch tasks", error)
        return { pageCount: 0, tableRows: [] }
      })
    },
    [adminParam],
  )

  const fetchTaskStats = useCallback(() => {
    return axios
      .get(apiUrl("/tasks/stats"), { params: { ...adminParam } })
      .then((response) => response.data)
      .catch((error) => {
        console.error("Failed to fetch task stats", error)
        return {
          queued_total: 0,
          queued_downloads: 0,
          queued_transcripts: 0,
          processing: 0,
          failed: 0,
          retry: 0,
          completed_24h: 0,
        }
      })
  }, [adminParam])

  const fetchMediaStats = useCallback((search?: string, status?: string) => {
    if (offlineMode) return fetchOfflineStats(search)

    return axios
      .get(apiUrl(mediaApi.stats), {
        params: { search: searchParam(search), ...scopeParams(status), ...adminParam },
      })
      .then((response) => response.data)
      .catch((error) => {
        console.error("Failed to fetch media stats", error)
        return {
          total_downloads: 0,
          total_transcript_blocks: 0,
          downloads_with_transcripts: 0,
        }
      })
  }, [adminParam, offlineMode])

  return (
    <div className="min-h-screen pb-[env(safe-area-inset-bottom)]">
      <main
        className={`w-full lg:container mx-auto px-2 pt-4 md:px-4 md:pt-6 ${audioPlayer.visible ? (infoExpanded ? "pb-32 sm:pb-36" : "pb-24 sm:pb-28") : "pb-4 md:pb-6"}`}
      >
        <div className="w-full">
          <div className="w-full">
            <AnimatePresence mode="wait">
              {view === "downloads" ? (
                <motion.div
                  key="downloads"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <DownloadsCard
                    fetchDownloads={fetchDownloadJobs}
                    fetchTranscriptSegments={fetchTranscriptSegments}
                    fetchStats={fetchMediaStats}
                    downloadOptions={downloadOptions}
                    setDownloadOptions={setdownloadOptions}
                    // The offline library only holds finished downloads, and the
                    // DELETED/SKIPPED action sets are server writes — so a scope
                    // chosen before the switch must not survive it.
                    status={offlineMode ? "COMPLETE" : mediaStatus}
                    setStatus={setMediaStatus}
                    search={search}
                    setSearch={setSearch}
                    semanticSearch={semanticSearch}
                    setSemanticSearch={setSemanticSearch}
                    semanticWeight={semanticWeight}
                    setSemanticWeight={setSemanticWeight}
                    deepLink={deepLink}
                    onDeepLinkHandled={clearDeepLink}
                    audioElement={audioElement}
                    audioPageOpen={audioPageOpen}
                    onAudioPageChange={setAudioPageOpen}
                  />
                </motion.div>
              ) : view === "subscriptions" && offlineMode ? (
                <OfflineUnavailable key="subscriptions-offline" label="Subscriptions" />
              ) : view === "subscriptions" ? (
                <motion.div
                  key="subscriptions"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <SubscriptionsCard
                    options={subOptions}
                    setOptions={setSubOptions}
                    fetchData={fetchSubscriptions}
                    subscriptions={subscriptions}
                    setSubscriptions={setSubscriptions}
                    subscriptionSearch={subscriptionSearch}
                    setSubscriptionSearch={setSubscriptionSearch}
                  />
                </motion.div>
              ) : view === "clips" && offlineMode ? (
                <OfflineUnavailable key="clips-offline" label="Clips" />
              ) : view === "clips" ? (
                <motion.div
                  key="clips"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <ClipsCard />
                </motion.div>
              ) : view === "playlists" ? (
                <motion.div
                  key="playlists"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <PlaylistsCard />
                </motion.div>
              ) : view === "tasks" && offlineMode ? (
                <OfflineUnavailable key="tasks-offline" label="Tasks" />
              ) : view === "tasks" ? (
                <motion.div
                  key="tasks"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <TasksCard
                    fetchTasks={fetchTasks}
                    fetchStats={fetchTaskStats}
                  />
                </motion.div>
              ) : view === "stats" && offlineMode ? (
                <OfflineUnavailable key="stats-offline" label="Stats" />
              ) : view === "stats" ? (
                <motion.div
                  key="stats"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <StatsCard />
                </motion.div>
              ) : view === "settings" && offlineMode ? (
                <OfflineUnavailable key="settings-offline" label="Settings" />
              ) : view === "settings" && adminMode ? (
                <motion.div
                  key="settings"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <SettingsCard />
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>
        </div>
      </main>

      {/* Fixed wrapper carries the viewport pinning with NO transform/backdrop-filter;
          the framer-motion transform + backdrop-blur-sm live on the inner element so iOS
          Safari keeps the bar glued to the bottom (transform on a fixed element there
          re-anchors it to the document → drift on scroll). */}
      <div className="fixed bottom-0 left-0 right-0 z-50 pointer-events-none">
        <AnimatePresence>
          {audioPlayer.visible && (
            <motion.div
              initial={{ y: 100, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              exit={{ y: 100, opacity: 0 }}
              transition={{ type: "spring", damping: 25, stiffness: 300 }}
              className="pointer-events-auto bg-bg-terminal/95 backdrop-blur-sm border-t border-border overflow-hidden pb-[env(safe-area-inset-bottom)]"
            >
            {visualizerStyle && (
              <AudioVisualizer
                analyserRef={analyserHandleRef}
                enabled
                style={visualizerStyle}
                className="pointer-events-none absolute inset-0 z-0"
              />
            )}
            <div className="relative z-10 w-full lg:container mx-auto px-2 sm:px-4">
              <div className="flex flex-col sm:flex-row items-center gap-1 py-1.5 sm:gap-4 sm:py-3">
                {/* Track Info + Close (mobile) */}
                <div className="flex items-center w-full sm:w-auto sm:flex-1 sm:min-w-0 gap-2">
                  <div className="flex-1 min-w-0 text-center sm:text-left">
                    <p className="font-mono text-sm text-text-primary truncate">
                      {audioPlayer.title}
                    </p>
                    <p className="text-xs text-text-secondary truncate">
                      {audioPlayer.channel}
                      {audioPlayer.playlistName && (
                        <span className="ml-2 text-matrix">
                          {audioPlayer.currentIndex !== undefined &&
                          audioPlayer.playlistMedia
                            ? `[${audioPlayer.currentIndex + 1}/${audioPlayer.playlistMedia.length}] ${audioPlayer.playlistName}`
                            : audioPlayer.playlistName}
                        </span>
                      )}
                    </p>
                    <AnimatePresence initial={false}>
                      {infoExpanded && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2 }}
                          className="overflow-hidden"
                        >
                          {nextTrack && (
                            <p className="text-xs text-text-muted mt-0.5 truncate">
                              Next up:{" "}
                              <span className="text-text-secondary">
                                {nextTrack.title}
                              </span>
                            </p>
                          )}
                          {audioPlayer.rating !== undefined && (
                            <div className="flex justify-center sm:justify-start mt-0.5">
                              <StarRating
                                rating={audioPlayer.rating}
                                onRate={(r) => rateMedia(r)}
                                compact
                              />
                            </div>
                          )}
                          {audioPlayer.url && (
                            <a
                              href={audioPlayer.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="block text-xs text-text-muted hover:text-matrix transition-colors font-mono truncate"
                            >
                              {audioPlayer.url}
                            </a>
                          )}
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                  {/* Expand/collapse details toggle */}
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => setInfoExpanded((v) => !v)}
                    className="h-8 w-8 shrink-0 text-text-muted"
                    title={infoExpanded ? "Hide details" : "Show details"}
                    aria-label={infoExpanded ? "Hide details" : "Show details"}
                    aria-expanded={infoExpanded}
                  >
                    {infoExpanded ? (
                      <ChevronUpIcon className="h-4 w-4" />
                    ) : (
                      <ChevronDownIcon className="h-4 w-4" />
                    )}
                  </Button>
                  {!offlineMode && !audioPlayer.isClip && (
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={openAudioPage}
                      className="h-8 w-8 shrink-0 text-text-muted sm:hidden"
                      title="Open this track's page and transcript"
                      aria-label="Open this track's page and transcript"
                    >
                      <DocumentTextIcon className="h-4 w-4" />
                    </Button>
                  )}
                  {/* Close button inline on mobile */}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={closeAudio}
                    className="shrink-0 sm:hidden"
                  >
                    Close
                  </Button>
                </div>

                {/* Playlist Controls */}
                {audioPlayer.playlistId && (
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={playPrevious}
                      disabled={!canGoPrevious}
                      className="h-8 w-8"
                      title="Previous track"
                    >
                      <BackwardIcon className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={playNext}
                      disabled={!nextTrack}
                      className="h-8 w-8"
                      title="Next track"
                    >
                      <ForwardIcon className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={toggleAutoplay}
                      className={`h-8 w-8 ${audioPlayer.autoplayEnabled ? "text-matrix" : "text-text-muted"}`}
                      title={
                        audioPlayer.autoplayEnabled
                          ? "Autoplay on"
                          : "Autoplay off"
                      }
                    >
                      <ArrowPathIcon className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={toggleShuffle}
                      className={`h-8 w-8 ${audioPlayer.shuffleEnabled ? "text-matrix" : "text-text-muted"}`}
                      title={
                        audioPlayer.shuffleEnabled
                          ? "Shuffle on"
                          : "Shuffle off"
                      }
                    >
                      <ArrowsRightLeftIcon className="h-4 w-4" />
                    </Button>
                  </div>
                )}

                <div className="flex-1 w-full sm:w-auto">
                  <AudioPlayer
                    id={audioPlayer.media_details_id}
                    startTime={audioPlayer.start_time}
                    exactStart={audioPlayer.exact_start}
                    duration={audioPlayer.duration}
                    isClip={audioPlayer.isClip}
                    visualizerStyle={visualizerStyle}
                    onCycleVisualizer={cycleVisualizer}
                    analyserHandleRef={analyserHandleRef}
                    queue={mediaSessionQueue}
                    onElement={setAudioElement}
                    onEnded={() => {
                      if (
                        audioPlayer.autoplayEnabled &&
                        audioPlayer.playlistId
                      ) {
                        playNext()
                      }
                    }}
                  />
                </div>

                {!offlineMode && !audioPlayer.isClip && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={openAudioPage}
                    className="shrink-0 hidden sm:inline-flex gap-1.5"
                    title="Open this track's page and transcript"
                  >
                    <DocumentTextIcon className="h-4 w-4" />
                    Transcript
                  </Button>
                )}
                {/* Close Button - desktop only */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={closeAudio}
                  className="shrink-0 hidden sm:inline-flex"
                >
                  Close
                </Button>
              </div>
            </div>
          </motion.div>
        )}
        </AnimatePresence>
      </div>
    </div>
  )
}
