"use client"

import { useState, useEffect, useCallback, useMemo, useRef, Dispatch, SetStateAction } from "react"
import { useFetchEffect } from "@/app/_hooks/useFetchEffect"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { BookOpenIcon, ArrowLeftIcon, MagnifyingGlassIcon, CalendarIcon, EyeIcon, ChevronDownIcon } from "@heroicons/react/20/solid"
import { ArrowDownTrayIcon } from "@heroicons/react/24/outline"
import { MediaListView } from "@/app/_components/media/MediaListView"
import { GroupBySelector } from "@/app/_components/GroupBySelector"
import { ViewToggle } from "@/app/_components/ViewToggle"
import { useViewMode } from "@/app/_hooks/useViewMode"
import { GroupFolderGrid } from "@/app/_components/GroupFolderGrid"
import { GroupBreadcrumb } from "@/app/_components/GroupBreadcrumb"
import { useDownloadGrouping } from "@/app/_hooks/useDownloadGrouping"
import { useElementDuration } from "@/app/_hooks/useElementDuration"
import { useTriStateSort } from "@/app/_hooks/useTriStateSort"
import { useResumePlayback } from "@/app/_hooks/useResumePlayback"
import { MediaBulkActions } from "@/app/_components/media/MediaBulkActions"
import { useMediaPlayer, LIBRARY_MIX_PLAYLIST_ID } from "@/app/context/MediaPlayerContext"
import { useOffline } from "@/app/context/OfflineContext"
import { DownloadButton } from "./DownloadButton"
import { TablePagination } from "@/app/_components/TablePagination"
import { VideoPlayer } from "@/app/_components/MediaPlayer"
import { InlinePlaylistVideoPlayer } from "@/app/_components/InlinePlaylistVideoPlayer"
import { VideoClippingControls } from "@/app/_components/VideoClippingControls"
import { MediaClipEditor } from "@/app/_components/media/MediaClipEditor"
import { Download, DownloadOptionsType, SortDirection, MediaStats, TagInfo, GroupLeafFilter } from "../types/DownloadsOptions"
import type { PlaylistMedia } from "@/app/types/PlaylistOptions"
import { TranscriptSegmentTable } from "./TranscriptSegmentTable"
import { MediaStatsBar } from "./MediaStatsBar"
import { FiltersPopover } from "./FiltersPopover"
import { ScopeSelector } from "./ScopeSelector"
import { PlaybackControls } from "./PlaybackControls"
import { StarRating } from "./StarRating"
import { Slider } from "@/components/ui/slider"
import { Separator } from "@/components/ui/separator"
import { motion, AnimatePresence } from "framer-motion"
import { formatDate } from "@/app/utils"
import axios from "axios"
import toast from "react-hot-toast"
import { apiUrl } from "@/app/lib/api"
import { mediaApi } from "@/app/lib/mediaApi"
import { offlineTags } from "@/app/lib/offlineLibrary"
import type { DeepLink } from "@/app/lib/deepLink"
import { useDeepLinkPlayback, type DeepLinkMedia } from "@/app/_hooks/useDeepLinkPlayback"
import { saveRating } from "@/app/_hooks/useMediaActions"

// Stable identity so an empty selection doesn't re-render the table every pass.
const NO_SELECTION: Set<number> = new Set()

// Same reason: these are what the key-tagged derivations below fall back to
// when the tag doesn't match, so they must not be fresh literals.
type VideoMetadata = {
  release_timestamp?: string
  access_count?: number
  rating?: number | null
}
const NO_METADATA: VideoMetadata = {}
const NO_SEMANTIC_ROWS: any[] = []

const QUEUE_POOL_LIMIT = 1000

// The list endpoint keys rows on `id`; the queue needs them keyed the way both
// the media list and the player expect.
type MediaListRecord = Omit<Download, "media_details_id"> & { id: number }
type QueueRow = Download & PlaylistMedia

const TRANSCRIPT_TABLE_HEAD = [
  "Score",
  "FTS",
  "Text",
  "Channel",
  "Title",
  "Start",
]

type DownloadsCardProps = {
  fetchDownloads: (
    search: string,
    status: string | null,
    pageNumber: number,
    sortBy?: string | null,
    sortDirection?: SortDirection,
    tagIds?: number[] | null,
    minRating?: number | null,
    groupFilter?: GroupLeafFilter | null,
    pageSize?: number
  ) => Promise<any>
  fetchTranscriptSegments: (
    standard_search: string,
    semantic_search: string,
    semanticWeight: number,
    filters: {
      tagIds: number[]
      minRating: number | null
      groupFilter: GroupLeafFilter | null
    }
  ) => Promise<any>
  fetchStats: (search?: string, status?: string) => Promise<MediaStats>
  downloadOptions: DownloadOptionsType
  setDownloadOptions: (downloadOptions: DownloadOptionsType) => void
  status: string
  setStatus: Dispatch<SetStateAction<string>>
  search: string
  setSearch: Dispatch<SetStateAction<string>>
  semanticSearch: string
  setSemanticSearch: Dispatch<SetStateAction<string>>
  semanticWeight: number
  setSemanticWeight: Dispatch<SetStateAction<number>>
  /** A `/?media=&t=` link the page was opened with, played once on arrival. */
  deepLink: DeepLink | null
  onDeepLinkHandled: () => void
}

export function DownloadsCard({
  fetchDownloads,
  fetchTranscriptSegments,
  fetchStats,
  downloadOptions,
  setDownloadOptions,
  status,
  setStatus,
  search,
  setSearch,
  semanticSearch: semanticSearchProp,
  setSemanticSearch,
  semanticWeight,
  setSemanticWeight,
  deepLink,
  onDeepLinkHandled,
}: DownloadsCardProps) {
  const { offlineMode } = useOffline()
  // Offline mode is playback-only. The transcript search is a server query, so a
  // query typed before the switch is treated as empty rather than left to fail.
  const semanticSearch = offlineMode ? "" : semanticSearchProp
  const [tableRows, setTableRows] = useState<any[]>([])
  const [semanticState, setSemanticState] = useState<{ key: string; rows: any[] }>({
    key: "",
    rows: NO_SEMANTIC_ROWS,
  })
  const [pageCount, setPageCount] = useState(0)
  // Tagged with the group folder it was paged within — see below, next to
  // `grouping`, where the two are combined.
  const [page, setPage] = useState<{ leafKey: string | null; n: number }>({
    leafKey: null,
    n: 1,
  })
  const [semanticPage, setSemanticPage] = useState<{ key: string; n: number }>({
    key: "",
    n: 1,
  })
  const SEMANTIC_PAGE_SIZE = 15
  const [stats, setStats] = useState<MediaStats | null>(null)

  const [selectedTagIds, setSelectedTagIds] = useState<number[]>([])
  const [minRating, setMinRating] = useState<number | null>(null)
  const [allTags, setAllTags] = useState<TagInfo[]>([])

  // Multi-select state. Tagged with the filter/page combination it was made
  // under — see NO_SELECTION below.
  const [selection, setSelection] = useState<{ key: string; ids: Set<number> }>({
    key: "",
    ids: NO_SELECTION,
  })
  const {
    mediaPlayer,
    openAudioPlayer,
    closeAudioPlayer,
    openVideoPlayer,
    closeVideoPlayer,
    playMediaQueue,
    toggleShuffle,
    detachQueue,
  } = useMediaPlayer()
  const [resumeEnabled, setResumeEnabled] = useResumePlayback(LIBRARY_MIX_PLAYLIST_ID)

  const [showDownloadForm, setShowDownloadForm] = useState(false)
  const [viewMode, setViewMode] = useViewMode("downloads")

  // openVideoPlayer clears every queue field, so the standalone path must never
  // run for a track the queue is driving — and a transcript jump must still be
  // able to take the pane over from a queue that is mid-play.
  const [videoSource, setVideoSource] = useState<"standalone" | "queue" | null>(null)
  const displayVideo = videoSource !== null
  const queueActive = mediaPlayer.playlistId === LIBRARY_MIX_PLAYLIST_ID
  const queuePlaying =
    queueActive && (mediaPlayer.audioVisible || mediaPlayer.videoVisible)
  const queueMode: "off" | "ordered" | "shuffled" = !queuePlaying
    ? "off"
    : mediaPlayer.shuffleEnabled
      ? "shuffled"
      : "ordered"

  // A playing clip carries the clip's own id here (ClipsTable), which can collide
  // with a library row's — so clips anchor and highlight nothing.
  const playingId =
    (mediaPlayer.audioVisible || mediaPlayer.videoVisible) && !mediaPlayer.isClip
      ? mediaPlayer.media_details_id
      : undefined

  // Overridden rather than written, so the stored preference comes back after.
  const resumeActive = queueMode === "off" && resumeEnabled

  // A ref because the reader is a cleanup, which closes over a render where this
  // was still false.
  const queueActiveRef = useRef(false)
  useEffect(() => {
    queueActiveRef.current = queueActive
  }, [queueActive])

  // The whole filtered set, which the paged list never holds. Keyed on the filter
  // it was fetched for, and dropped by an explicit refresh — the poll only
  // refills the visible page.
  const queuePool = useRef<{ key: string; rows: QueueRow[] } | null>(null)
  const [queueLoading, setQueueLoading] = useState(false)
  // Clip editor target. Audio and video both go through MediaClipEditor, so the
  // scissors action lands on the same surface here as it does in playlists.
  const [clipTarget, setClipTarget] = useState<Download | null>(null)
  const { sortBy, sortDirection, handleSort } = useTriStateSort()
  const [videoCurrentTime, setVideoCurrentTime] = useState(0)
  const videoRefRef = useRef<HTMLVideoElement | null>(null)
  // Only feeds the clip editor. VideoPlayer keeps taking the stored duration, so
  // its seek effect doesn't re-fire (and re-seek) when metadata lands.
  const { duration: videoElementDuration, refCallback: handleVideoRef } =
    useElementDuration(videoRefRef)

  // Grid view only exists for COMPLETE media; SKIPPED and DELETED are table-only.
  // Derived rather than written back into viewMode, because viewMode is now a
  // persisted preference — forcing "table" on it would clobber the user's choice
  // the moment they clicked "Show Deleted".
  const effectiveViewMode = status === "COMPLETE" ? viewMode : "table"

  // "Group by" folder navigation for the grid view (COMPLETE status only)
  const grouping = useDownloadGrouping({
    enabled: effectiveViewMode === "grid" && status === "COMPLETE" && !offlineMode,
    status,
    search,
    tagIds: selectedTagIds,
    minRating,
  })

  // Drilling into or out of a group folder is a different list, so the page
  // number doesn't carry over. Deriving it covers every path that moves the leaf
  // — openFolder, goUp, setGroupDim and the reset when grouping is switched off.
  const pageNumber = page.leafKey === grouping.leafKey ? page.n : 1
  const setPageNumber = useCallback(
    (n: number) => setPage({ leafKey: grouping.leafKey, n }),
    [grouping.leafKey]
  )

  // Drilling into a tag folder overrides the tag chips, exactly as it does for the
  // media list — see `loadDownloads`.
  const effectiveTagIds = grouping.scope?.tagIds ?? selectedTagIds

  // Both the rows and the page reset when the query changes — same
  // value-plus-its-key derivation as `selectedIds` and `pageNumber` above. It
  // carries the whole scope, not just the two search strings, so results fetched
  // inside one folder are never shown under another.
  const semanticKey = JSON.stringify([
    semanticSearch,
    search,
    semanticWeight,
    grouping.scopeKey,
    effectiveTagIds,
    minRating,
  ])
  const semanticTableRows =
    semanticState.key === semanticKey ? semanticState.rows : NO_SEMANTIC_ROWS
  const semanticPageNumber = semanticPage.key === semanticKey ? semanticPage.n : 1
  const setSemanticPageNumber = useCallback(
    (n: number) => setSemanticPage({ key: semanticKey, n }),
    [semanticKey]
  )

  const [rowSelect, setRowSelect] = useState<{
    media_details_id: number
    title: string
    channel: string
    url: string
    duration: number
    playback_position: number
    thumbnail_path?: string
    /**
     * Set when playback_position carries a timestamp the user picked (a
     * transcript hit) rather than a resume position, so a hit near the end of a
     * video isn't bounced back to 0.
     */
    exact_start?: boolean
  }>({
    media_details_id: 0,
    title: "",
    channel: "",
    url: "",
    duration: 0,
    playback_position: 0,
  })
  // Tagged with the video it describes, so closing the player or switching
  // videos drops the old metadata by comparison during render rather than by an
  // effect that resets it. handleVideoRate still writes it optimistically.
  const [metadataState, setMetadataState] = useState<{
    key: string
    data: VideoMetadata
  }>({ key: "", data: NO_METADATA })
  const metadataKey =
    displayVideo && rowSelect.media_details_id ? String(rowSelect.media_details_id) : ""
  const videoMetadata = metadataState.key === metadataKey ? metadataState.data : NO_METADATA

  useFetchEffect(
    (signal) =>
      axios
        .get(apiUrl(mediaApi.detail(rowSelect.media_details_id)), { signal })
        .then((response) => {
          const data = response.data
          setMetadataState({
            key: metadataKey,
            data: {
              release_timestamp: data.release_timestamp,
              access_count: data.access_count,
              rating: data.rating ?? null,
            },
          })
        })
        .catch((err) => {
          if (axios.isCancel(err)) return
          console.error("Failed to fetch media details:", err)
        }),
    [metadataKey, rowSelect.media_details_id],
    { enabled: metadataKey !== "" && !offlineMode }
  )

  // Opens the inline queue player when the queue reaches a VIDEO track, which
  // autoplay can do without any click here.
  useEffect(() => {
    if (!queueActive) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- local state the player mirrors into, not derives from: both play paths and the back button write it too
    setVideoSource(mediaPlayer.videoVisible ? "queue" : null)
  }, [queueActive, mediaPlayer.videoVisible])

  // Feed the shared media-session metadata so the iOS lock screen shows the video's
  // thumbnail during standalone (non-playlist) playback, matching audio. Cleared on
  // return-to-library / tab-switch unmount so videoVisible never leaks into the
  // playlist video surface.
  //
  // Queue tracks are excluded: startQueue/playNext already populate all of this,
  // and openVideoPlayer would wipe the queue out.
  useEffect(() => {
    if (videoSource !== "standalone" || !rowSelect.media_details_id) return
    openVideoPlayer({
      media_details_id: rowSelect.media_details_id,
      title: rowSelect.title,
      channel: rowSelect.channel,
      thumbnail_path: rowSelect.thumbnail_path,
      duration: rowSelect.duration,
      start_time: rowSelect.playback_position,
    })
    // A queue adopting this video flips videoSource to "queue", firing this
    // cleanup — which would close the pane the queue just opened.
    return () => {
      if (!queueActiveRef.current) closeVideoPlayer()
    }
  }, [
    videoSource,
    rowSelect.media_details_id,
    rowSelect.title,
    rowSelect.channel,
    rowSelect.thumbnail_path,
    rowSelect.duration,
    rowSelect.playback_position,
    openVideoPlayer,
    closeVideoPlayer,
  ])

  const handleVideoRate = async (mediaId: number, rating: number | null) => {
    const previousRating = videoMetadata.rating
    const patchRating = (value: number | null | undefined) =>
      setMetadataState((prev) => ({ ...prev, data: { ...prev.data, rating: value } }))
    patchRating(rating)
    try {
      await saveRating(mediaId, rating)
    } catch {
      toast.error('Failed to update rating')
      patchRating(previousRating)
    }
  }

  const loadTags = useCallback(() => {
    const tags = offlineMode
      ? offlineTags()
      : axios.get(apiUrl(mediaApi.allTags)).then((response) => response.data as TagInfo[])
    tags.then(setAllTags).catch(() => {})
  }, [offlineMode])

  useEffect(() => {
    loadTags()
  }, [loadTags])

  const loadStats = useCallback(
    () =>
      fetchStats(search, status)
        .then((data) => setStats(data))
        .catch(() => {}),
    [fetchStats, search, status]
  )

  const handleInputChange = (event: { target: { value: string } }) => {
    setSearch(event.target.value)
    setPageNumber(1)
  }

  const handleSemanticInputChange = (event: { target: { value: string } }) => {
    setSemanticSearch(event.target.value)
  }

  const loadDownloads = useCallback(() => {
    // When drilled into a group folder, the leaf overrides the tag filter and adds
    // channel/untagged/date params so this reuses the normal paginated list path.
    const leaf = grouping.leaf
    return fetchDownloads(
      search,
      status,
      pageNumber,
      sortBy,
      sortDirection,
      leaf ? effectiveTagIds : selectedTagIds,
      minRating,
      leaf?.filter ?? null
    )
      .then(({ pageCount: newPageCount, tableRows }) => {
        setPageCount(newPageCount)
        setTableRows(
          tableRows.map((row: any) => ({
            ...row,
            media_details_id: row.id,
          }))
        )
        // If we're on a page beyond available data (e.g. deleted last item on last page),
        // navigate back to the last available page
        if (tableRows.length === 0 && pageNumber > 1 && newPageCount > 0) {
          setPageNumber(newPageCount)
        }
      })
      .catch(() => {})
  }, [
    fetchDownloads,
    grouping.leaf,
    search,
    status,
    pageNumber,
    sortBy,
    sortDirection,
    effectiveTagIds,
    selectedTagIds,
    minRating,
    setPageNumber,
  ])

  // The media list is hidden behind the video player, the clip editor, group
  // folders and semantic search, so none of those should be fetching it. Stats
  // share the condition: they poll alongside the list, as they did when both
  // lived in one interval.
  const listActive =
    !semanticSearch &&
    !(search.length > 0 && search.length < 3) &&
    !displayVideo &&
    !clipTarget &&
    !grouping.showFolders

  const { isLoading: downloadsFetching, refetch: reloadDownloads } = useFetchEffect(
    loadDownloads,
    [loadDownloads],
    { enabled: listActive, pollMs: 10_000 }
  )

  const { isLoading: statsFetching, refetch: reloadStats } = useFetchEffect(
    loadStats,
    [loadStats],
    { pollMs: listActive ? 10_000 : null }
  )
  const statsLoading = statsFetching

  const handleRefresh = useCallback(() => {
    queuePool.current = null
    reloadDownloads()
    reloadStats()
    loadTags()
  }, [reloadDownloads, reloadStats, loadTags])

  const patchRow = useCallback(
    (mediaId: number, patch: Partial<Download>) =>
      setTableRows((rows) =>
        rows.map((r) =>
          r.media_details_id === mediaId ? { ...r, ...patch } : r,
        ),
      ),
    [],
  )

  // `selectionKey` minus the page (a queue spans every page of a filter), plus the
  // visible row ids so a finished download or a deletion drops the pool while an
  // unchanged poll tick keeps it.
  const queuePoolKey =
    JSON.stringify([
      status,
      search,
      sortBy,
      sortDirection,
      selectedTagIds,
      minRating,
      grouping.leafKey,
    ]) +
    "|" +
    (tableRows as Download[]).map((row) => row.media_details_id).join(",")

  // Surfaces as the footer's `[n/N] <name>` badge and the lock screen's "album".
  const queueName = useMemo(() => {
    const parts: string[] = []
    if (search.length > 2) parts.push(`"${search}"`)
    const tagNames = allTags
      .filter((t) => selectedTagIds.includes(t.id))
      .map((t) => t.name)
    if (tagNames.length > 0) parts.push(tagNames.join(" + "))
    if (minRating != null) parts.push(`${minRating}★+`)
    const leafLabels = grouping.breadcrumb.segments.map((seg) => seg.label)
    if (grouping.atLeaf && leafLabels.length > 0) parts.push(leafLabels.join(" / "))
    return parts.length > 0 ? `Library · ${parts.join(" · ")}` : "Library"
  }, [search, allTags, selectedTagIds, minRating, grouping.atLeaf, grouping.breadcrumb])

  const loadQueuePool = useCallback(async (force: boolean): Promise<QueueRow[]> => {
    if (!force && queuePool.current?.key === queuePoolKey) return queuePool.current.rows

    const key = queuePoolKey
    setQueueLoading(true)
    try {
      const { tableRows: records } = await fetchDownloads(
        search,
        status,
        1,
        sortBy,
        sortDirection,
        grouping.leaf?.tagIds ?? selectedTagIds,
        minRating,
        grouping.leaf?.filter ?? null,
        QUEUE_POOL_LIMIT
      )
      // Only rows with a file can join the queue.
      const rows: QueueRow[] = (records as MediaListRecord[])
        .filter((row) => row.file_path)
        .map((row) => ({
          ...row,
          media_details_id: row.id,
          playlist_id: LIBRARY_MIX_PLAYLIST_ID,
          position: 0,
          added_at: row.downloaded_at || row.created_at || "",
        }))
      // Not cached when empty: fetchDownloads resolves an error to an empty page,
      // so caching one would poison this filter until it changes or is refreshed.
      if (rows.length > 0) queuePool.current = { key, rows }
      return rows
    } finally {
      setQueueLoading(false)
    }
  }, [
    queuePoolKey,
    fetchDownloads,
    search,
    status,
    sortBy,
    sortDirection,
    grouping.leaf,
    selectedTagIds,
    minRating,
  ])

  const anchored =
    (fallback: (rows: QueueRow[]) => number | undefined) => (rows: QueueRow[]) =>
      playingId && rows.some((r) => r.media_details_id === playingId)
        ? playingId
        : fallback(rows)

  const startQueueFrom = useCallback(
    async (
      pick: (rows: QueueRow[]) => number | undefined,
      shuffle: boolean
    ): Promise<boolean> => {
      const resolves = (
        rows: QueueRow[],
        target: number | undefined
      ): target is number =>
        target !== undefined && rows.some((r) => r.media_details_id === target)

      const wasCached = queuePool.current?.key === queuePoolKey
      let rows: QueueRow[]
      let target: number | undefined
      try {
        rows = await loadQueuePool(false)
        target = pick(rows)
        // startQueue falls back to index 0 for a target it can't find, so a pool
        // cached before this row existed would silently play something else.
        if (wasCached && !resolves(rows, target)) {
          rows = await loadQueuePool(true)
          target = pick(rows)
        }
      } catch {
        toast.error("Failed to load the playback queue")
        return false
      }
      if (!resolves(rows, target)) return false

      playMediaQueue({
        playlistId: LIBRARY_MIX_PLAYLIST_ID,
        playlistName: queueName,
        media: rows,
        shuffle,
        targetMediaDetailsId: target,
        resume: false,
        continueCurrent:
          target === playingId
            ? // Audio stays mounted in the footer, so any start_time re-seeks it.
              // A standalone video hands over to the inline player and remounts,
              // so that one needs the live position.
              {
                at:
                  videoSource === "standalone"
                    ? videoRefRef.current?.currentTime
                    : undefined,
              }
            : undefined,
      })
      return true
    },
    [loadQueuePool, queuePoolKey, playMediaQueue, queueName, playingId, videoSource]
  )

  const reportEmptyQueue = (started: boolean) => {
    if (!started) toast.error("No playable media matches the current filter")
  }

  const playStandalone = (row: Download) => {
    const startAt = resumeActive ? row.playback_position || 0 : 0
    setRowSelect({
      media_details_id: row.media_details_id,
      title: row.title,
      channel: row.channel,
      url: row.url,
      duration: row.duration || 0,
      playback_position: startAt,
      thumbnail_path: row.thumbnail_path,
      exact_start: false,
    })
    if (row.media_type === "VIDEO") {
      closeAudioPlayer()
      setVideoSource("standalone")
    } else if (row.media_type === "AUDIO") {
      setVideoSource(null)
      openAudioPlayer({
        media_details_id: row.media_details_id,
        title: row.title,
        channel: row.channel,
        url: row.url,
        start_time: startAt,
        duration: row.duration,
        thumbnail_path: row.thumbnail_path,
      })
    }
  }

  const handleMediaOpen = (row: Download) => {
    if (!row.file_path) {
      toast.error("Media file not available")
      return
    }
    if (queueMode === "off") {
      playStandalone(row)
      return
    }
    void startQueueFrom(() => row.media_details_id, queueMode === "shuffled").then(
      (started) => {
        if (!started) playStandalone(row)
      }
    )
  }

  const handlePlayAll = () => {
    if (queueMode === "ordered") return detachQueue(LIBRARY_MIX_PLAYLIST_ID)
    if (queueMode === "shuffled") return toggleShuffle()
    void startQueueFrom(anchored((rows) => rows[0]?.media_details_id), false).then(
      reportEmptyQueue
    )
  }

  const handleShuffle = () => {
    if (queueMode === "shuffled") return detachQueue(LIBRARY_MIX_PLAYLIST_ID)
    if (queueMode === "ordered") return toggleShuffle()
    void startQueueFrom(
      anchored((rows) => rows[Math.floor(Math.random() * rows.length)]?.media_details_id),
      true
    ).then(reportEmptyQueue)
  }

  // Unscoped by playlistId, unlike PlaylistDetailView: the library is every track's
  // home, so it lights up whatever is playing. Not queuePlaying — that derives
  // queueMode, which drives the Play All / Shuffle button states.
  const nowPlayingClass = useCallback(
    (item: Download) =>
      playingId === item.media_details_id ? "bg-matrix/10" : undefined,
    [playingId]
  )

  // Same shape as a transcript-hit jump: the linked second stands even near the end.
  const playLinked = (media: DeepLinkMedia, seconds: number) => {
    setRowSelect({
      media_details_id: media.id,
      title: media.title,
      channel: media.channel,
      url: media.url,
      duration: media.duration || 0,
      playback_position: seconds,
      thumbnail_path: media.thumbnail_path,
      exact_start: true,
    })
  }
  useDeepLinkPlayback(deepLink, offlineMode, {
    onVideo: (media, seconds) => {
      playLinked(media, seconds)
      closeAudioPlayer()
      setVideoSource("standalone")
    },
    onAudio: (media, seconds) => {
      playLinked(media, seconds)
      setVideoSource(null)
      openAudioPlayer({
        media_details_id: media.id,
        title: media.title,
        channel: media.channel,
        url: media.url,
        start_time: seconds,
        exact_start: true,
        duration: media.duration ?? undefined,
        thumbnail_path: media.thumbnail_path,
      })
    },
    onHandled: onDeepLinkHandled,
  })

  const showTranscriptVideo = useCallback(
    (visible: boolean) => setVideoSource(visible ? "standalone" : null),
    []
  )

  /**
   * Table row click. Unlike a grid card, a DELETED row opens the source URL and
   * a SKIPPED row fills the download form — the file itself isn't playable.
   */
  const handleRowActivate = (row: Download) => {
    if (status === "DELETED") {
      if (row.url) window.open(row.url, "_blank", "noopener,noreferrer")
      return
    }
    if (status === "SKIPPED") {
      populateDownloadFromSkipped(row)
      return
    }
    handleMediaOpen(row)
  }

  const handleMediaClip = (row: Download) => {
    if (!row.file_path) {
      toast.error("Media file not available")
      return
    }
    if (row.media_type !== "AUDIO" && row.media_type !== "VIDEO") return
    setClipTarget(row)
  }

  const populateDownloadFromSkipped = (row: Download) => {
    setDownloadOptions({
      ...downloadOptions,
      url: row.url,
      audio_only: row.media_type === "AUDIO",
      overwrite: false,
    })
    toast.success(`Download options populated for: ${row.title}`)
    window.scrollTo({ top: 0, behavior: "smooth" })
  }

  // Multi-select derived values.
  //
  // A selection only means anything for the rows it was made on, so it carries
  // the filter/page combination it belongs to and stops applying the moment that
  // changes. Deriving the clear beats an effect that re-renders to do the same.
  const selectionKey = JSON.stringify([
    pageNumber,
    status,
    search,
    sortBy,
    sortDirection,
    selectedTagIds,
    minRating,
  ])
  const selectedIds = selection.key === selectionKey ? selection.ids : NO_SELECTION
  const setSelectedIds = useCallback(
    (ids: Set<number>) => setSelection({ key: selectionKey, ids }),
    [selectionKey]
  )

  const selectedItems = (tableRows as Download[]).filter((r) => selectedIds.has(r.media_details_id))
  const allSelected = tableRows.length > 0 && tableRows.every((r: any) => selectedIds.has(r.media_details_id))
  const selectionActive = status === "COMPLETE" && effectiveViewMode === "table" && !offlineMode

  const handleSelectAll = (selected: boolean) => {
    if (selected) {
      setSelectedIds(new Set(tableRows.map((r: any) => r.media_details_id)))
    } else {
      setSelectedIds(new Set())
    }
  }

  // Semantic results, tagged with the query they belong to, so clearing or
  // retyping the query drops them by comparison during render rather than
  // needing an effect to reset them.
  const { isLoading: semanticFetching } = useFetchEffect(
    () =>
      fetchTranscriptSegments(search, semanticSearch, semanticWeight, {
        tagIds: effectiveTagIds,
        minRating,
        groupFilter: grouping.scope?.filter ?? null,
      }).then((rows: any[]) =>
        setSemanticState({
          key: semanticKey,
          rows: rows.map((row: any) => ({
            ...row,
            media_details_id: row.media_details.id,
          })),
        })
      ),
    [
      semanticKey,
      search,
      semanticSearch,
      semanticWeight,
      effectiveTagIds,
      minRating,
      grouping.scope,
      fetchTranscriptSegments,
    ],
    { enabled: semanticSearch.length >= 3 }
  )

  const loading = downloadsFetching || semanticFetching

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="space-y-4"
    >
      {!offlineMode && (
        <div className="md:hidden">
          <button
            onClick={() => setShowDownloadForm(!showDownloadForm)}
            className="w-full inline-flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-mono transition-colors bg-bg-surface text-text-muted hover:text-text-secondary border border-border"
          >
            <ArrowDownTrayIcon className="h-3.5 w-3.5" />
            New Download
            <ChevronDownIcon className={`h-3.5 w-3.5 transition-transform ${showDownloadForm ? "rotate-180" : ""}`} />
          </button>
          <AnimatePresence>
            {showDownloadForm && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden"
              >
                <div className="pt-2">
                  <DownloadButton
                    options={downloadOptions}
                    setOptions={setDownloadOptions}
                  />
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {!offlineMode && (
        <div className="hidden md:block">
          <DownloadButton
            options={downloadOptions}
            setOptions={setDownloadOptions}
          />
        </div>
      )}

      <Card>
        {clipTarget ? (
          <CardContent className="pt-6">
            <MediaClipEditor
              media={clipTarget}
              onBack={() => setClipTarget(null)}
              backLabel="Return to Library"
            />
          </CardContent>
        ) : videoSource === "queue" ? (
          <CardContent className="pt-6 space-y-4">
            <InlinePlaylistVideoPlayer
              backLabel="Return to Library"
              onReturn={() => setVideoSource(null)}
              videoRefCallback={handleVideoRef}
              onTimeUpdate={setVideoCurrentTime}
            />
            {!offlineMode && (
              <VideoClippingControls
                mediaDetailsId={mediaPlayer.media_details_id}
                duration={mediaPlayer.duration || videoElementDuration}
                currentTime={videoCurrentTime}
                onSeek={(time) => {
                  if (videoRefRef.current) {
                    videoRefRef.current.currentTime = time
                  }
                }}
                videoRef={videoRefRef}
              />
            )}
          </CardContent>
        ) : displayVideo ? (
          <CardContent className="pt-6 space-y-4">
            <Button
              variant="outline"
              onClick={() => setVideoSource(null)}
              className="gap-2 mt-4"
            >
              <ArrowLeftIcon className="h-4 w-4" />
              Return to Library
            </Button>
            <VideoPlayer
              id={rowSelect.media_details_id}
              startTime={rowSelect.playback_position}
              duration={rowSelect.duration}
              exactStart={rowSelect.exact_start}
              onTimeUpdate={setVideoCurrentTime}
              videoRefCallback={handleVideoRef}
            />
            <div className="text-center space-y-1">
              <h3 className="font-mono text-lg text-text-primary">
                {rowSelect.title}
              </h3>
              <p className="text-sm text-text-secondary">{rowSelect.channel}</p>
              {videoMetadata.rating !== undefined && (
                <div className="flex justify-center mt-0.5">
                  <StarRating
                    rating={videoMetadata.rating}
                    onRate={(r) => handleVideoRate(rowSelect.media_details_id, r)}
                  />
                </div>
              )}
              {rowSelect.url && (
                <a
                  href={rowSelect.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-text-muted hover:text-matrix transition-colors font-mono truncate block"
                >
                  {rowSelect.url}
                </a>
              )}
              {(videoMetadata.release_timestamp || videoMetadata.access_count !== undefined) && (
                <div className="flex items-center justify-center gap-4 text-xs text-text-muted font-mono mt-1">
                  {videoMetadata.release_timestamp && (
                    <span className="flex items-center gap-1">
                      <CalendarIcon className="h-3 w-3" />
                      {formatDate(videoMetadata.release_timestamp)}
                    </span>
                  )}
                  {videoMetadata.access_count !== undefined && (
                    <span className="flex items-center gap-1">
                      <EyeIcon className="h-3 w-3" />
                      {videoMetadata.access_count} {videoMetadata.access_count === 1 ? "play" : "plays"}
                    </span>
                  )}
                </div>
              )}
            </div>
            {!offlineMode && (
              <VideoClippingControls
                mediaDetailsId={rowSelect.media_details_id}
                duration={rowSelect.duration || videoElementDuration}
                currentTime={videoCurrentTime}
                onSeek={(time) => {
                  if (videoRefRef.current) {
                    videoRefRef.current.currentTime = time
                  }
                }}
                videoRef={videoRefRef}
              />
            )}
          </CardContent>
        ) : (
          <>
            <CardHeader className="pb-3">
              <div className="flex flex-row items-center justify-between gap-2 sm:gap-3 overflow-x-auto [&::-webkit-scrollbar]:hidden">
                <div className="flex items-center flex-nowrap shrink-0 gap-1.5 sm:gap-3">
                  {!offlineMode && (
                    <ScopeSelector
                      value={status}
                      onChange={(next) => {
                        setStatus(next)
                        setPageNumber(1)
                      }}
                    />
                  )}
                  {status === "COMPLETE" && (
                    <>
                      <FiltersPopover
                        allTags={allTags}
                        selectedTagIds={selectedTagIds}
                        onTagsChange={(ids) => { setSelectedTagIds(ids); setPageNumber(1) }}
                        minRating={minRating}
                        onRatingChange={(r) => { setMinRating(r); setPageNumber(1) }}
                      />
                      <div className="flex items-center gap-1.5 sm:gap-3">
                        <Separator orientation="vertical" className="h-4" />
                        <ViewToggle mode={effectiveViewMode} onChange={setViewMode} />
                        {effectiveViewMode === "grid" && (
                          <GroupBySelector
                            value={grouping.groupDim}
                            onChange={grouping.setGroupDim}
                          />
                        )}
                      </div>
                      {!semanticSearch && (
                        <div className="flex items-center gap-1.5 sm:gap-3">
                          <Separator orientation="vertical" className="h-4" />
                          <PlaybackControls
                            queueMode={queueMode}
                            disabled={queueLoading}
                            onPlayAll={handlePlayAll}
                            onShuffle={handleShuffle}
                            resume={{
                              checked: resumeActive,
                              disabled: queueMode !== "off",
                              onChange: setResumeEnabled,
                            }}
                          />
                        </div>
                      )}
                    </>
                  )}
                </div>
                <div className="shrink-0">
                  <MediaStatsBar stats={stats} loading={statsLoading} />
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4 px-0">
              <div className="flex flex-col sm:flex-row gap-3">
                <div className="relative flex-1">
                  <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-text-muted" />
                  <Input
                    placeholder="Search downloads  (use && / ||)..."
                    title={'Combine terms: "lofi && mix" matches both, "cats || dogs" matches either. Each term matches channel or title.'}
                    value={search}
                    onChange={handleInputChange}
                    className="pl-9"
                  />
                </div>
                {!offlineMode && (
                  <div className="relative flex-1">
                    <BookOpenIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-text-muted" />
                    <Input
                      placeholder="Semantic transcript search..."
                      value={semanticSearch}
                      onChange={handleSemanticInputChange}
                      className="pl-9"
                    />
                  </div>
                )}
              </div>

              {semanticSearch.length >= 3 && (
                <div className="flex items-center gap-3">
                  <span className="text-xs font-mono text-text-muted whitespace-nowrap">Keyword</span>
                  <Slider
                    value={semanticWeight * 100}
                    onChange={(v) => setSemanticWeight(v / 100)}
                    min={0}
                    max={100}
                    step={5}
                    className="flex-1"
                  />
                  <span className="text-xs font-mono text-text-muted whitespace-nowrap">Semantic</span>
                  <span className="text-xs font-mono text-matrix w-8 text-right">{Math.round(semanticWeight * 100)}%</span>
                </div>
              )}

              {selectionActive && (
                <MediaBulkActions
                  selectedItems={selectedItems}
                  allTags={allTags}
                  onClearSelection={() => setSelectedIds(new Set())}
                  onRefresh={handleRefresh}
                />
              )}

              <div className={semanticSearch || effectiveViewMode === "table" ? "md:rounded-lg md:border md:border-border overflow-hidden" : ""}>
                {semanticSearch ? (
                  <>
                  {grouping.isGrouping && (
                    <GroupBreadcrumb
                      breadcrumb={grouping.breadcrumb}
                      canGoUp={grouping.groupPath.length > 0}
                      onGoUp={grouping.goUp}
                    />
                  )}
                  <TranscriptSegmentTable
                    tableColumns={TRANSCRIPT_TABLE_HEAD}
                    tableRows={semanticTableRows.slice(
                      (semanticPageNumber - 1) * SEMANTIC_PAGE_SIZE,
                      semanticPageNumber * SEMANTIC_PAGE_SIZE
                    )}
                    loading={loading}
                    setRowSelect={setRowSelect}
                    setDisplayVideo={showTranscriptVideo}
                    searchQuery={semanticSearch}
                  />
                  </>
                ) : effectiveViewMode === "grid" ? (
                  grouping.showFolders ? (
                    <GroupFolderGrid
                      breadcrumb={grouping.breadcrumb}
                      folders={grouping.folders}
                      loading={grouping.foldersLoading}
                      pageCount={grouping.foldersPageCount}
                      pageNumber={grouping.folderPage}
                      setPageNumber={grouping.setFolderPage}
                      canGoUp={grouping.groupPath.length > 0}
                      onGoUp={grouping.goUp}
                      onOpen={grouping.openFolder}
                    />
                  ) : (
                  <>
                  {grouping.atLeaf && (
                    <GroupBreadcrumb
                      breadcrumb={grouping.breadcrumb}
                      canGoUp
                      onGoUp={grouping.goUp}
                    />
                  )}
                  <MediaListView
                    viewMode="grid"
                    rows={tableRows as Download[]}
                    loading={loading}
                    status={status}
                    onRefresh={handleRefresh}
                    patchRow={patchRow}
                    sortBy={sortBy}
                    sortDirection={sortDirection}
                    onSort={handleSort}
                    allTags={allTags}
                    onTagsChange={loadTags}
                    onRowClick={handleMediaOpen}
                    onClip={handleMediaClip}
                    onPopulateSkipped={populateDownloadFromSkipped}
                    showPlaybackProgress={resumeActive}
                    rowClassName={nowPlayingClass}
                  />
                  </>
                  )
                ) : (
                  <MediaListView
                    viewMode="table"
                    rows={!semanticSearch ? (tableRows as Download[]) : []}
                    loading={loading}
                    status={status}
                    onRefresh={handleRefresh}
                    patchRow={patchRow}
                    sortBy={sortBy}
                    sortDirection={sortDirection}
                    onSort={handleSort}
                    allTags={allTags}
                    onTagsChange={loadTags}
                    onRowClick={handleRowActivate}
                    onClip={handleMediaClip}
                    onPopulateSkipped={populateDownloadFromSkipped}
                    showPlaybackProgress={resumeActive}
                    rowClassName={nowPlayingClass}
                    {...(selectionActive && {
                      selection: {
                        selectedIds,
                        onSelectionChange: setSelectedIds,
                        allSelected,
                        onSelectAll: handleSelectAll,
                        idOf: (row: Download) => row.media_details_id,
                      },
                    })}
                  />
                )}
              </div>

              {semanticSearch ? (
                Math.ceil(semanticTableRows.length / SEMANTIC_PAGE_SIZE) > 1 && (
                  <TablePagination
                    pageNumber={semanticPageNumber}
                    pageCount={Math.ceil(semanticTableRows.length / SEMANTIC_PAGE_SIZE)}
                    setPageNumber={setSemanticPageNumber}
                  />
                )
              ) : grouping.showFolders ? null : (
                <TablePagination
                  pageNumber={pageNumber}
                  pageCount={pageCount}
                  setPageNumber={setPageNumber}
                />
              )}
            </CardContent>
          </>
        )}
      </Card>

    </motion.div>
  )
}
