"use client"
import { createContext, useContext, useState, useCallback, useEffect, useRef, ReactNode, useMemo } from "react"
import { PlaylistMedia } from "@/app/types/PlaylistOptions"
import axios from "axios"
import { apiUrl } from "@/app/lib/api"
import { mediaApi } from "@/app/lib/mediaApi"
import { saveRating } from "@/app/_hooks/useMediaActions"
import { useOffline } from "@/app/context/OfflineContext"

// Virtual (non-DB) playlist ids for on-the-fly queues: the tag-based "Tag Mix",
// and the media library's current filter. Negative so they can never collide
// with a real playlist id, and truthy so autoplay and footer context activate.
export const TAG_MIX_PLAYLIST_ID = -1
export const LIBRARY_MIX_PLAYLIST_ID = -2

const isVirtualPlaylist = (playlistId: number) => playlistId < 0

/**
 * The track the queue moves to when the current one ends, with the start
 * position playNext() will give it. Null when nothing would autoplay.
 */
export type AutoAdvanceTarget = {
  id: number
  startTime: number
  duration?: number
  mediaType: 'AUDIO' | 'VIDEO' | undefined
}

type MediaPlayerState = {
  audioVisible: boolean
  videoVisible: boolean

  // Shared state (used by both)
  media_details_id: number
  title: string
  channel: string
  start_time?: number
  exact_start?: boolean
  duration?: number
  url?: string
  release_timestamp?: string
  access_count?: number
  rating?: number | null
  isClip?: boolean
  thumbnail_path?: string

  // Playlist fields
  playlistId?: number
  playlistName?: string
  playlistMedia?: PlaylistMedia[]
  currentIndex?: number
  autoplayEnabled: boolean
  shuffleEnabled: boolean
  originalPlaylistMedia?: PlaylistMedia[]
  /** Start each track at its saved playback_position instead of at 0. */
  resumeEnabled: boolean

  activeMediaType: 'AUDIO' | 'VIDEO' | null
}

type MediaPlayerContextType = {
  mediaPlayer: MediaPlayerState

  // `mediaPlayer` with the audio pane's `audioVisible` flattened to `visible`.
  audioPlayer: MediaPlayerState & { visible: boolean }

  openAudioPlayer: (audio: Omit<MediaPlayerState, 'autoplayEnabled' | 'shuffleEnabled' | 'resumeEnabled' | 'audioVisible' | 'videoVisible' | 'activeMediaType'> & { autoplayEnabled?: boolean; visible?: boolean }) => void
  // Standalone (non-playlist) video playback. Populates the shared metadata so the
  // lock-screen Media Session shows the video's thumbnail, mirroring openAudioPlayer.
  openVideoPlayer: (video: Pick<MediaPlayerState, 'media_details_id' | 'title' | 'channel'> & Partial<Pick<MediaPlayerState, 'thumbnail_path' | 'duration' | 'start_time'>>) => void
  closeAudioPlayer: () => void
  closeVideoPlayer: () => void

  playPlaylist: (playlistId: number, playlistName: string, shuffle: boolean, targetMediaDetailsId: number, resume: boolean) => Promise<void>
  // Play an already-resolved queue (no fetch) — used for virtual/on-the-fly playlists
  playMediaQueue: (args: {
    playlistId: number
    playlistName: string
    media: PlaylistMedia[]
    shuffle: boolean
    targetMediaDetailsId: number
    resume: boolean
    /**
     * Continue the track already on air instead of seeding it from `resume`.
     * `at` is only for a surface that remounts anyway; otherwise `start_time` is
     * left untouched, since useMediaElement re-seeks whenever it changes.
     */
    continueCurrent?: { at?: number }
  }) => void
  playNext: () => void
  playPrevious: () => void
  toggleAutoplay: () => void
  toggleShuffle: () => void

  // Re-point a live queue at a changed track list. Both no-op unless the queue
  // is playing `playlistId`, so call sites can fire them unconditionally.
  replaceQueue: (playlistId: number, media: PlaylistMedia[]) => void
  syncPlaylistQueue: (playlistId: number) => Promise<void>

  // Apply a resume-toggle change to the queue that's already playing.
  setQueueResume: (playlistId: number, enabled: boolean) => void

  detachQueue: (playlistId: number) => void

  /**
   * Positions the player has persisted this session, keyed by media id, so a
   * list can show progress for tracks it fetched before they were played.
   *
   * Every track played is kept, not just the current one: a list's rows are a
   * snapshot from when it was fetched, so on autoplaying to the next track the
   * finished one would otherwise snap back to the position it had at page load.
   */
  savedPositions: Record<number, number>
  notePlaybackPosition: (mediaDetailsId: number, position: number) => void

  rateMedia: (rating: number | null) => Promise<void>

  nextTrack: PlaylistMedia | null
  autoAdvance: AutoAdvanceTarget | null
}

const MediaPlayerContext = createContext<MediaPlayerContextType | undefined>(
  undefined
)

export function useMediaPlayer() {
  const ctx = useContext(MediaPlayerContext)
  if (!ctx)
    throw new Error("useMediaPlayer must be used within MediaPlayerProvider")
  return ctx
}

function shuffleArray<T>(array: T[]): T[] {
  const shuffled = [...array]
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]]
  }
  return shuffled
}

/**
 * Where a queue track starts. A never-played track has no position at all, and
 * neither does any row from a queue fetched without include_playback.
 *
 * A position at the very end of a track is left to useMediaElement, which
 * restarts anything within 30s of the end.
 */
function resumeStart(media: PlaylistMedia, resume: boolean): number {
  return resume ? (media.playback_position ?? 0) : 0
}

/** Splices `current` back into a refreshed queue that no longer contains it. */
function retainCurrent(rows: PlaylistMedia[], current: PlaylistMedia, at: number): PlaylistMedia[] {
  if (rows.some((m) => m.media_details_id === current.media_details_id)) return rows
  const index = Math.min(Math.max(at, 0), rows.length)
  return [...rows.slice(0, index), current, ...rows.slice(index)]
}

export function MediaPlayerProvider({ children }: { children: ReactNode }) {
  const { offlineMode } = useOffline()
  const [mediaPlayer, setMediaPlayer] = useState<MediaPlayerState>({
    audioVisible: false,
    videoVisible: false,
    media_details_id: 0,
    title: "",
    channel: "",
    autoplayEnabled: true,
    shuffleEnabled: false,
    resumeEnabled: false,
    activeMediaType: null,
  })

  const [savedPositions, setSavedPositions] = useState<Record<number, number>>({})

  const notePlaybackPosition = useCallback((mediaDetailsId: number, position: number) => {
    setSavedPositions((prev) =>
      prev[mediaDetailsId] === position ? prev : { ...prev, [mediaDetailsId]: position }
    )
  }, [])

  const audioPlayer = useMemo(() => ({
    ...mediaPlayer,
    visible: mediaPlayer.audioVisible,
  }), [mediaPlayer])

  // Lets syncPlaylistQueue skip the fetch for a playlist that isn't playing —
  // the common case, since most reordering happens with the player idle or on
  // some other playlist.
  const playlistIdRef = useRef<number | undefined>(undefined)
  useEffect(() => {
    playlistIdRef.current = mediaPlayer.playlistId
  }, [mediaPlayer.playlistId])

  // Read by syncPlaylistQueue, which must ask for playback positions on a
  // refetch of a resuming queue or replaceQueue would strip them back out.
  const resumeRef = useRef(false)
  useEffect(() => {
    resumeRef.current = mediaPlayer.resumeEnabled
  }, [mediaPlayer.resumeEnabled])

  // A sync whose ticket is stale by the time it resolves is dropped, so two fast
  // drags can't apply out of order and a sync can't clobber a queue the user
  // started while it was in flight (startQueue bumps it too).
  const syncSeqRef = useRef(0)

  const nextTrack = useMemo(() => {
    if (!mediaPlayer.playlistMedia || mediaPlayer.currentIndex === undefined) {
      return null
    }
    const nextIndex = mediaPlayer.currentIndex + 1
    if (nextIndex >= mediaPlayer.playlistMedia.length) {
      return null
    }
    return mediaPlayer.playlistMedia[nextIndex]
  }, [mediaPlayer.playlistMedia, mediaPlayer.currentIndex])

  // Must agree with what playNext() derives, since the player starts this
  // track on its own before playNext's state lands (see useMediaElement).
  const autoAdvance = useMemo<AutoAdvanceTarget | null>(() => {
    if (!nextTrack || !mediaPlayer.autoplayEnabled || !mediaPlayer.playlistId) return null
    return {
      id: nextTrack.media_details_id,
      startTime: resumeStart(nextTrack, mediaPlayer.resumeEnabled),
      duration: nextTrack.duration,
      mediaType: nextTrack.media_type as 'AUDIO' | 'VIDEO' | undefined,
    }
  }, [nextTrack, mediaPlayer.autoplayEnabled, mediaPlayer.playlistId, mediaPlayer.resumeEnabled])

  useEffect(() => {
    if (!mediaPlayer.media_details_id) return
    // Offline the queue entry already carries everything playback needs; what
    // this adds (rating, plays, source url) is server state, shown only online.
    if (offlineMode) return

    const controller = new AbortController()
    axios
      .get(apiUrl(mediaApi.detail(mediaPlayer.media_details_id)), {
        signal: controller.signal,
      })
      .then((response) => {
        const data = response.data
        setMediaPlayer((prev) => {
          // Only update if we're still on the same media
          if (prev.media_details_id !== data.id) return prev
          return {
            ...prev,
            release_timestamp: data.release_timestamp ?? undefined,
            access_count: data.access_count ?? undefined,
            rating: data.rating ?? null,
            url: data.url ?? prev.url ?? undefined,
            // Resolves lock-screen artwork for openers that lack it (e.g. clips,
            // whose Clip type carries no thumbnail_path).
            thumbnail_path: prev.thumbnail_path ?? data.thumbnail_path ?? undefined,
          }
        })
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          console.error("Failed to fetch media details:", err)
        }
      })

    return () => controller.abort()
  }, [mediaPlayer.media_details_id, offlineMode])

  const openAudioPlayer = useCallback((audio: Omit<MediaPlayerState, 'autoplayEnabled' | 'shuffleEnabled' | 'resumeEnabled' | 'audioVisible' | 'videoVisible' | 'activeMediaType'> & { autoplayEnabled?: boolean; visible?: boolean }) => {
    setMediaPlayer((prev) => ({
      ...prev,
      ...audio,
      rating: audio.media_details_id === prev.media_details_id ? prev.rating : undefined,
      audioVisible: true,
      videoVisible: false,
      activeMediaType: 'AUDIO',
      autoplayEnabled: audio.autoplayEnabled ?? prev.autoplayEnabled,
      exact_start: audio.exact_start ?? false,
      playlistId: audio.playlistId ?? undefined,
      playlistName: audio.playlistName ?? undefined,
      playlistMedia: audio.playlistMedia ?? undefined,
      currentIndex: audio.currentIndex ?? undefined,
      resumeEnabled: false,
    }))
  }, [])

  // Standalone video (library "Return to Library" player, clip player). The video
  // element itself is rendered locally by those views; this only feeds the shared
  // metadata so MediaPlayerContext's mediaSession effect sets the lock-screen artwork.
  const openVideoPlayer = useCallback((video: Pick<MediaPlayerState, 'media_details_id' | 'title' | 'channel'> & Partial<Pick<MediaPlayerState, 'thumbnail_path' | 'duration' | 'start_time'>>) => {
    setMediaPlayer((prev) => ({
      ...prev,
      media_details_id: video.media_details_id,
      title: video.title,
      channel: video.channel,
      thumbnail_path: video.thumbnail_path,
      duration: video.duration,
      start_time: video.start_time ?? 0,
      rating: video.media_details_id === prev.media_details_id ? prev.rating : undefined,
      audioVisible: false,
      videoVisible: true,
      activeMediaType: 'VIDEO',
      // Standalone playback - clear any playlist context so no stale album / next-track leaks.
      playlistId: undefined,
      playlistName: undefined,
      playlistMedia: undefined,
      currentIndex: undefined,
      originalPlaylistMedia: undefined,
      resumeEnabled: false,
    }))
  }, [])

  const closeAudioPlayer = useCallback(() =>
    setMediaPlayer((p) => ({
      ...p,
      audioVisible: false,
      activeMediaType: p.videoVisible ? 'VIDEO' : null,
    })), [])

  const closeVideoPlayer = useCallback(() =>
    setMediaPlayer((p) => ({
      ...p,
      videoVisible: false,
      activeMediaType: p.audioVisible ? 'AUDIO' : null,
    })), [])

  // Shared engine: start playback of an already-resolved media list under a
  // (real or virtual) playlist id. Used by both playPlaylist and playMediaQueue.
  const startQueue = useCallback((playlistId: number, playlistName: string, media: PlaylistMedia[], shuffle: boolean, targetMediaDetailsId: number, resume: boolean, continueCurrent?: { at?: number }) => {
    if (media.length === 0) return

    syncSeqRef.current++

    const found = media.findIndex(m => m.media_details_id === targetMediaDetailsId)
    const resolvedIndex = found >= 0 ? found : 0

    let playlistMedia: PlaylistMedia[]
    let originalPlaylistMedia: PlaylistMedia[] | undefined
    let playIndex: number

    if (shuffle) {
      originalPlaylistMedia = media
      const startTrack = media[resolvedIndex]
      const rest = media.filter((_, i) => i !== resolvedIndex)
      playlistMedia = [startTrack, ...shuffleArray(rest)]
      playIndex = 0
    } else {
      playlistMedia = media
      originalPlaylistMedia = undefined
      playIndex = resolvedIndex
    }

    const currentMedia = playlistMedia[playIndex]
    const mediaType = currentMedia.media_type as 'AUDIO' | 'VIDEO' | undefined

    const isVideo = mediaType === 'VIDEO'
    const isAudio = mediaType === 'AUDIO'

    setMediaPlayer((prev) => {
      const adopting = !!continueCurrent && prev.media_details_id === currentMedia.media_details_id

      return {
        audioVisible: isAudio,
        videoVisible: isVideo,
        media_details_id: currentMedia.media_details_id,
        title: currentMedia.title || "Unknown",
        channel: currentMedia.channel || "",
        duration: currentMedia.duration,
        thumbnail_path: currentMedia.thumbnail_path,
        rating: currentMedia.media_details_id === prev.media_details_id ? prev.rating : undefined,
        isClip: false,
        playlistId,
        playlistName,
        playlistMedia,
        currentIndex: playIndex,
        autoplayEnabled: true,
        shuffleEnabled: shuffle,
        originalPlaylistMedia,
        resumeEnabled: resume,
        activeMediaType: isVideo ? 'VIDEO' : isAudio ? 'AUDIO' : null,
        start_time: adopting ? (continueCurrent.at ?? prev.start_time) : resumeStart(currentMedia, resume),
        // Preserved too, or adopting a transcript jump re-seeks to its timestamp.
        exact_start: adopting ? (prev.exact_start ?? false) : false,
      }
    })
  }, [])

  // Fetch all media in the playlist. light=true skips the per-user rating, tag
  // and transcript lookups the queue never reads — this runs on every play click
  // and every mutation of a playing playlist, over up to 1000 rows.
  const fetchPlaylistQueue = useCallback(async (playlistId: number, includePlayback: boolean): Promise<PlaylistMedia[]> => {
    const response = await axios.get(
      apiUrl(`/playlists/${playlistId}/media`),
      { params: { page_size: 1000, light: true, include_playback: includePlayback } }
    )
    return response.data.records
  }, [])

  const playPlaylist = useCallback(async (playlistId: number, playlistName: string, shuffle: boolean, targetMediaDetailsId: number, resume: boolean) => {
    try {
      const media = await fetchPlaylistQueue(playlistId, resume)
      startQueue(playlistId, playlistName, media, shuffle, targetMediaDetailsId, resume)
    } catch (error) {
      console.error("Failed to load playlist:", error)
    }
  }, [fetchPlaylistQueue, startQueue])

  const playMediaQueue = useCallback((args: {
    playlistId: number
    playlistName: string
    media: PlaylistMedia[]
    shuffle: boolean
    targetMediaDetailsId: number
    resume: boolean
    continueCurrent?: { at?: number }
  }) => {
    startQueue(args.playlistId, args.playlistName, args.media, args.shuffle, args.targetMediaDetailsId, args.resume, args.continueCurrent)
  }, [startQueue])

  /**
   * Re-points a live queue at a changed track list, without disturbing the track
   * on air. The only place queue membership changes after startQueue.
   *
   * Keyed by media_details_id throughout, which is unique per playlist (the
   * playlist_media unique constraint), so a track is always found exactly once.
   */
  const replaceQueue = useCallback((playlistId: number, media: PlaylistMedia[]) => {
    setMediaPlayer((prev) => {
      if (prev.playlistId !== playlistId || !prev.playlistMedia || prev.currentIndex === undefined) {
        return prev
      }
      const currentTrack = prev.playlistMedia[prev.currentIndex]
      if (!currentTrack) return prev

      // The playing track holds its slot even if it just left the playlist:
      // playlistMedia[currentIndex] being the track on air is an invariant
      // toggleShuffle and the footer's [n/N] badge both read. Applied to the
      // incoming order first so it survives into originalPlaylistMedia too,
      // which is what un-shuffling later restores from.
      const order = retainCurrent(media, currentTrack, prev.currentIndex)

      let next: PlaylistMedia[]
      if (prev.shuffleEnabled) {
        // Reconcile membership only: a reorder of the underlying playlist must
        // not reshuffle what's already queued up. Entries are re-read from
        // `order` so their `position` reflects the new arrangement.
        const byId = new Map(order.map((m) => [m.media_details_id, m]))
        const kept = prev.playlistMedia
          .map((m) => byId.get(m.media_details_id))
          .filter((m): m is PlaylistMedia => m !== undefined)
        const keptIds = new Set(kept.map((m) => m.media_details_id))
        next = [...kept, ...order.filter((m) => !keptIds.has(m.media_details_id))]
      } else {
        next = order
      }

      return {
        ...prev,
        playlistMedia: next,
        originalPlaylistMedia: prev.shuffleEnabled ? order : undefined,
        currentIndex: next.findIndex((m) => m.media_details_id === currentTrack.media_details_id),
      }
    })
  }, [])

  // The override exists for setQueueResume, which calls this in the same tick it
  // flips the flag — before the commit-phase effect has refreshed resumeRef.
  const syncPlaylistQueue = useCallback(async (playlistId: number, includePlayback?: boolean) => {
    if (playlistIdRef.current !== playlistId) return

    const ticket = ++syncSeqRef.current
    try {
      const media = await fetchPlaylistQueue(playlistId, includePlayback ?? resumeRef.current)
      if (ticket !== syncSeqRef.current) return
      replaceQueue(playlistId, media)
    } catch (error) {
      console.error("Failed to sync playback queue:", error)
    }
  }, [fetchPlaylistQueue, replaceQueue])

  /**
   * Applies a resume-toggle change to the queue on air, so the setting and what
   * the next track does can't disagree. Enabling refetches, because a real
   * playlist's queue is fetched light and carries no positions until asked for.
   *
   * A virtual playlist is skipped rather than special-cased later: it has no
   * endpoint to refetch from, and its rows come from the media list already
   * carrying positions. So does an offline queue, whose rows are the stored
   * media records — and whose endpoint is unreachable anyway.
   */
  const setQueueResume = useCallback((playlistId: number, enabled: boolean) => {
    if (playlistIdRef.current !== playlistId) return
    setMediaPlayer((prev) => (
      prev.playlistId === playlistId ? { ...prev, resumeEnabled: enabled } : prev
    ))
    if (enabled && !isVirtualPlaylist(playlistId) && !offlineMode) {
      void syncPlaylistQueue(playlistId, true)
    }
  }, [syncPlaylistQueue, offlineMode])

  /**
   * Drops the queue while the current track plays on, stopping only at its end.
   *
   * Leaving media_details_id and start_time alone is the whole mechanism: both
   * players are driven by `id` + `startTime`, so writing either reloads the
   * element and restarts the track.
   */
  const detachQueue = useCallback((playlistId: number) => {
    setMediaPlayer((prev) =>
      prev.playlistId === playlistId
        ? {
            ...prev,
            playlistId: undefined,
            playlistName: undefined,
            playlistMedia: undefined,
            currentIndex: undefined,
            originalPlaylistMedia: undefined,
            shuffleEnabled: false,
          }
        : prev
    )
  }, [])

  const playNext = useCallback(() => {
    setMediaPlayer((prev) => {
      if (!prev.playlistMedia || prev.currentIndex === undefined) return prev

      const nextIndex = prev.currentIndex + 1
      if (nextIndex >= prev.playlistMedia.length) {
        return {
          ...prev,
          audioVisible: false,
          videoVisible: false,
          activeMediaType: null,
        }
      }

      const nextMedia = prev.playlistMedia[nextIndex]
      const mediaType = nextMedia.media_type as 'AUDIO' | 'VIDEO' | undefined
      const isVideo = mediaType === 'VIDEO'
      const isAudio = mediaType === 'AUDIO'

      return {
        ...prev,
        media_details_id: nextMedia.media_details_id,
        title: nextMedia.title || "Unknown",
        channel: nextMedia.channel || "",
        duration: nextMedia.duration,
        thumbnail_path: nextMedia.thumbnail_path,
        rating: nextMedia.media_details_id === prev.media_details_id ? prev.rating : undefined,
        url: undefined,
        currentIndex: nextIndex,
        start_time: resumeStart(nextMedia, prev.resumeEnabled),
        exact_start: false,
        audioVisible: isAudio,
        videoVisible: isVideo,
        activeMediaType: isVideo ? 'VIDEO' : isAudio ? 'AUDIO' : null,
      }
    })
  }, [])

  const playPrevious = useCallback(() => {
    setMediaPlayer((prev) => {
      if (!prev.playlistMedia || prev.currentIndex === undefined) return prev

      const prevIndex = prev.currentIndex - 1
      if (prevIndex < 0) return prev

      const prevMedia = prev.playlistMedia[prevIndex]
      const mediaType = prevMedia.media_type as 'AUDIO' | 'VIDEO' | undefined
      const isVideo = mediaType === 'VIDEO'
      const isAudio = mediaType === 'AUDIO'

      return {
        ...prev,
        media_details_id: prevMedia.media_details_id,
        title: prevMedia.title || "Unknown",
        channel: prevMedia.channel || "",
        duration: prevMedia.duration,
        thumbnail_path: prevMedia.thumbnail_path,
        rating: prevMedia.media_details_id === prev.media_details_id ? prev.rating : undefined,
        url: undefined,
        currentIndex: prevIndex,
        start_time: resumeStart(prevMedia, prev.resumeEnabled),
        exact_start: false,
        audioVisible: isAudio,
        videoVisible: isVideo,
        activeMediaType: isVideo ? 'VIDEO' : isAudio ? 'AUDIO' : null,
      }
    })
  }, [])

  const toggleAutoplay = useCallback(() => {
    setMediaPlayer((prev) => ({ ...prev, autoplayEnabled: !prev.autoplayEnabled }))
  }, [])

  const toggleShuffle = useCallback(() => {
    setMediaPlayer((prev) => {
      if (!prev.playlistMedia) return prev

      const currentTrackId = prev.currentIndex !== undefined
        ? prev.playlistMedia[prev.currentIndex]?.media_details_id
        : undefined

      if (!prev.shuffleEnabled) {
        // Turning ON
        const original = prev.originalPlaylistMedia || [...prev.playlistMedia]
        const currentTrack = prev.currentIndex !== undefined ? prev.playlistMedia[prev.currentIndex] : null
        const rest = prev.playlistMedia.filter((_, i) => i !== prev.currentIndex)
        const shuffled = shuffleArray(rest)

        return {
          ...prev,
          shuffleEnabled: true,
          originalPlaylistMedia: original,
          playlistMedia: currentTrack ? [currentTrack, ...shuffled] : shuffled,
          currentIndex: 0,
        }
      } else {
        // Turning OFF
        const original = prev.originalPlaylistMedia || prev.playlistMedia
        const newIndex = currentTrackId !== undefined
          ? original.findIndex(m => m.media_details_id === currentTrackId)
          : 0

        return {
          ...prev,
          shuffleEnabled: false,
          playlistMedia: original,
          originalPlaylistMedia: undefined,
          currentIndex: Math.max(0, newIndex),
        }
      }
    })
  }, [])

  const rateMedia = useCallback(async (rating: number | null) => {
    const mediaId = mediaPlayer.media_details_id
    if (!mediaId) return

    const previousRating = mediaPlayer.rating
    setMediaPlayer((prev) => ({ ...prev, rating }))

    try {
      await saveRating(mediaId, rating)
    } catch {
      setMediaPlayer((prev) => ({ ...prev, rating: previousRating }))
    }
  }, [mediaPlayer.media_details_id, mediaPlayer.rating])

  // Media Session metadata for lock screen controls (iOS/Android). Action
  // handlers deliberately live in useMediaSessionActionHandlers, next to the
  // media element - two owners of navigator.mediaSession clobber each other.
  useEffect(() => {
    if (typeof navigator === 'undefined' || !('mediaSession' in navigator)) return

    const isActive = mediaPlayer.audioVisible || mediaPlayer.videoVisible

    if (!isActive) {
      navigator.mediaSession.metadata = null
      return
    }

    const hasThumbnail = !!mediaPlayer.thumbnail_path
    const artworkSrc = hasThumbnail
      ? apiUrl(mediaApi.thumbnail(mediaPlayer.media_details_id))
      : '/artwork-512.png'
    // Thumbnail endpoint serves JPEG; the static fallback is PNG.
    const artworkType = hasThumbnail ? 'image/jpeg' : 'image/png'

    navigator.mediaSession.metadata = new MediaMetadata({
      title: mediaPlayer.title || 'Unknown',
      artist: mediaPlayer.channel || undefined,
      album: mediaPlayer.playlistName || undefined,
      artwork: [
        { src: artworkSrc, sizes: '96x96', type: artworkType },
        { src: artworkSrc, sizes: '256x256', type: artworkType },
        { src: artworkSrc, sizes: '512x512', type: artworkType },
      ],
    })

  }, [
    mediaPlayer.title,
    mediaPlayer.channel,
    mediaPlayer.playlistName,
    mediaPlayer.thumbnail_path,
    mediaPlayer.media_details_id,
    mediaPlayer.audioVisible,
    mediaPlayer.videoVisible,
  ])

  return (
    <MediaPlayerContext.Provider
      value={{
        mediaPlayer,
        audioPlayer,
        openAudioPlayer,
        openVideoPlayer,
        closeAudioPlayer,
        closeVideoPlayer,
        playPlaylist,
        playMediaQueue,
        playNext,
        playPrevious,
        toggleAutoplay,
        toggleShuffle,
        replaceQueue,
        syncPlaylistQueue,
        setQueueResume,
        detachQueue,
        savedPositions,
        notePlaybackPosition,
        rateMedia,
        nextTrack,
        autoAdvance,
      }}
    >
      {children}
    </MediaPlayerContext.Provider>
  )
}
