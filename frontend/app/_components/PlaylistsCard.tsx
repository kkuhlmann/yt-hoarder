"use client"

import { useState, useEffect, useCallback } from "react"
import { useFetchEffect } from "@/app/_hooks/useFetchEffect"
import { useTriStateSort } from "@/app/_hooks/useTriStateSort"
import { arrayMove } from "@dnd-kit/sortable"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { PlaylistDetailView } from "@/app/_components/playlists/PlaylistDetailView"
import { PlaylistCard } from "@/app/_components/playlists/PlaylistCard"
import { buildPlaylistColumns } from "@/app/_components/playlists/columns"
import { DataTable } from "@/app/_components/data/DataTable"
import { CardGrid } from "@/app/_components/data/CardGrid"
import { ActionList, actionsHeaderStrip } from "@/app/_components/data/ActionList"
import { CollectionOfflineButton } from "@/app/_components/CollectionOfflineButton"
import { playlistSource } from "@/app/lib/offlineSync"
import type { ActionDescriptor } from "@/app/_components/data/ActionList"
import { ShareDialog } from "@/app/_components/ShareDialog"
import { ViewToggle } from "@/app/_components/ViewToggle"
import { useViewMode } from "@/app/_hooks/useViewMode"
import { useAuth } from "@/app/context/AuthContext"
import { useOffline } from "@/app/context/OfflineContext"
import { fetchOfflinePlaylistMedia, fetchOfflinePlaylists } from "@/app/lib/offlinePlaylists"
import { TagMixView } from "@/app/_components/TagMixView"
import { MediaClipEditor } from "@/app/_components/media/MediaClipEditor"
import { InlinePlaylistVideoPlayer } from "@/app/_components/InlinePlaylistVideoPlayer"
import { TablePagination } from "@/app/_components/TablePagination"
import { CreatePlaylistDialog } from "@/app/_components/CreatePlaylistDialog"
import { Playlist, PlaylistTrack, SortDirection } from "@/app/types/PlaylistOptions"
import type { Download } from "@/app/types/DownloadsOptions"
import { useMediaPlayer } from "@/app/context/MediaPlayerContext"
import {
  MagnifyingGlassIcon,
  ArrowLeftIcon,
  ArrowPathIcon,
  PlusIcon,
  UserPlusIcon,
} from "@heroicons/react/20/solid"
import toast from "react-hot-toast"
import axios from "axios"
import { motion } from "framer-motion"
import { apiUrl } from "@/app/lib/api"
import { cn } from "@/lib/utils"
import { formatDuration } from "@/app/utils"

type PlaylistStatsBarProps = {
  totalPlaylists: number
  loading: boolean
}

function PlaylistStatsBar({ totalPlaylists, loading }: PlaylistStatsBarProps) {
  if (loading) {
    return (
      <div className="flex gap-4 text-sm text-text-muted font-mono animate-pulse">
        <span>Loading...</span>
      </div>
    )
  }

  return (
    <div className="flex gap-4 text-sm font-mono">
      <span className="text-text-secondary">
        Total: <span className="text-matrix">{totalPlaylists}</span>
      </span>
    </div>
  )
}

/** Sort fields offered on mobile, where column headers aren't available. */
const PLAYLIST_SORT_OPTIONS = [
  { key: "name", label: "Name" },
  { key: "created_at", label: "Created" },
  { key: "updated_at", label: "Updated" },
]

export function PlaylistsCard() {
  const { mediaPlayer } = useMediaPlayer()
  const { offlineMode } = useOffline()

  // Top-level mode: normal playlist list vs. the on-the-fly tag-based "Tag Mix".
  // Tag Mix is built from server queries, so offline mode pins the list.
  const [modeChoice, setMode] = useState<'playlists' | 'tagmix'>('playlists')
  const mode = offlineMode ? 'playlists' : modeChoice
  // Lifted so tag selection survives the inline-video early return (unmount/remount).
  const [mixTagIds, setMixTagIds] = useState<number[]>([])

  const [tableRows, setTableRows] = useState<Playlist[]>([])
  const [pageCount, setPageCount] = useState(0)
  const [pageNumber, setPageNumber] = useState(1)
  const [search, setSearch] = useState("")
  const { sortBy, sortDirection, handleSort } = useTriStateSort()
  const [totalPlaylists, setTotalPlaylists] = useState(0)

  const [selectedPlaylist, setSelectedPlaylist] = useState<Playlist | null>(null)
  const [playlistMedia, setPlaylistMedia] = useState<PlaylistTrack[]>([])
  const [mediaPageCount, setMediaPageCount] = useState(0)
  const [mediaPageNumber, setMediaPageNumber] = useState(1)
  const [mediaLoading, setMediaLoading] = useState(false)
  // Total across all pages: the move-down arrow has to know the real last
  // position, not the last one on screen.
  const [totalTracks, setTotalTracks] = useState(0)
  // Playlist order is the default and the only order reordering applies to.
  const [trackSortBy, setTrackSortBy] = useState<string>("position")
  const [trackSortDirection, setTrackSortDirection] = useState<SortDirection>("asc")

  const [listViewMode, setListViewMode] = useViewMode("playlists")
  const { user } = useAuth()
  const [shareTarget, setShareTarget] = useState<Playlist | null>(null)

  const [displayVideo, setDisplayVideo] = useState(false)

  // Clip editor, opened from a track's scissors action. Held here rather than in
  // the detail view so the editor can replace the whole card — the playlist
  // header and pagination have nothing to say while you're editing a clip.
  const [clipTarget, setClipTarget] = useState<Download | null>(null)

  // Mirror the player's video visibility so the inline video player opens whenever the
  // active queue reaches a video track — including autoplay/next in a mixed audio+video
  // queue — and closes again when it advances back to audio.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- local state the player mirrors into, not derives from: the back arrow and both onVideoPlayback handlers below write it too
    setDisplayVideo(mediaPlayer.videoVisible)
  }, [mediaPlayer.videoVisible])

  const [createDialogOpen, setCreateDialogOpen] = useState(false)

  const fetchPlaylists = useCallback(
    async (
      searchQuery: string,
      page: number,
      sortByParam: string | null,
      sortDirectionParam: SortDirection
    ) => {
      // Offline, the list is the playlists with something downloaded — same
      // envelope, read from the membership snapshot instead of the server.
      if (offlineMode) {
        return fetchOfflinePlaylists({
          search: searchQuery,
          pageNumber: page,
          sortBy: sortByParam,
          sortDirection: sortDirectionParam,
        })
      }

      const params: Record<string, string | number | null> = {
        page,
        sort_by: sortByParam,
        sort_direction: sortDirectionParam,
      }

      if (searchQuery && searchQuery.length >= 3) {
        params.search = searchQuery
      }

      const response = await axios.get(
        apiUrl('/playlists'),
        { params }
      )
      return response.data
    },
    [offlineMode]
  )

  const fetchPlaylistMedia = useCallback(
    async (
      playlistId: number,
      page: number,
      sortByParam: string,
      sortDirectionParam: SortDirection
    ) => {
      // Offline tracks come back in playlist order only; the detail view never
      // sorts them any other way.
      if (offlineMode) return fetchOfflinePlaylistMedia(playlistId, { pageNumber: page })

      const response = await axios.get(apiUrl(`/playlists/${playlistId}/media`), {
        params: {
          page,
          sort_by: sortByParam,
          sort_direction: sortDirectionParam ?? "asc",
        },
      })
      return response.data
    },
    [offlineMode]
  )

  const loadPlaylists = useCallback(
    () =>
      fetchPlaylists(search, pageNumber, sortBy, sortDirection)
        .then((data) => {
          setPageCount(data.page_count)
          setTableRows(data.records)
          setTotalPlaylists(data.count_records)
        })
        .catch(() => {}),
    [fetchPlaylists, search, pageNumber, sortBy, sortDirection]
  )

  // Loads on mount, on search/pagination/sort changes, and every 30s.
  const { isLoading: loading, refetch: reloadPlaylists } = useFetchEffect(
    loadPlaylists,
    [loadPlaylists],
    { enabled: search.length === 0 || search.length >= 3, pollMs: 30_000 }
  )

  const loadPlaylistMedia = useCallback(
    (
      playlistId: number,
      page: number = mediaPageNumber,
      sortByParam: string = trackSortBy,
      sortDirectionParam: SortDirection = trackSortDirection
    ) => {
      setMediaLoading(true)
      fetchPlaylistMedia(playlistId, page, sortByParam, sortDirectionParam)
        .then((data) => {
          setPlaylistMedia(data.records)
          setMediaPageCount(data.page_count)
          setTotalTracks(data.count_records)
          setMediaLoading(false)
        })
        .catch(() => setMediaLoading(false))
    },
    [fetchPlaylistMedia, mediaPageNumber, trackSortBy, trackSortDirection]
  )

  /** Optimistic single-row patch, for ratings and tags inside the detail view. */
  const patchTrack = useCallback(
    (mediaId: number, patch: Partial<Download>) =>
      setPlaylistMedia((rows) =>
        rows.map((r) =>
          r.media_details_id === mediaId ? { ...r, ...patch } : r
        )
      ),
    []
  )

  /**
   * Optimistic within-page move. `patchTrack` can't express this: every row
   * between the two ends changes its position, not just the one that moved.
   *
   * Positions are contiguous 1..N across the whole playlist and a page is a
   * contiguous window of that, so renumbering from the window's own first
   * position reproduces exactly what the backend computes — which is what makes
   * it safe to skip the refetch afterwards.
   */
  const reorderTracks = useCallback((fromIndex: number, toIndex: number) => {
    setPlaylistMedia((rows) => {
      if (
        fromIndex === toIndex ||
        fromIndex < 0 ||
        toIndex < 0 ||
        fromIndex >= rows.length ||
        toIndex >= rows.length
      ) {
        return rows
      }
      const base = rows[0].position
      return arrayMove(rows, fromIndex, toIndex).map((row, i) => ({
        ...row,
        position: base + i,
      }))
    })
  }, [])

  /** Rollback for a reorder the server rejected. */
  const restoreTracks = useCallback(
    (rows: PlaylistTrack[]) => setPlaylistMedia(rows),
    []
  )

  /**
   * Track sort cycling. Unlike the media library the null step isn't "unsorted"
   * — it returns to playlist order, which re-enables the reorder arrows. So a
   * third click on any header always gets you back to reordering.
   */
  const handleTrackSort = (column: string) => {
    let direction: SortDirection
    if (trackSortBy === column) {
      direction = trackSortDirection === "desc" ? "asc" : trackSortDirection === "asc" ? null : "desc"
    } else {
      direction = "desc"
    }

    const nextSortBy = direction === null ? "position" : column
    const nextDirection: SortDirection = direction === null ? "asc" : direction
    setTrackSortBy(nextSortBy)
    setTrackSortDirection(nextDirection)
    if (selectedPlaylist) {
      loadPlaylistMedia(selectedPlaylist.id, mediaPageNumber, nextSortBy, nextDirection)
    }
  }

  const handleInputChange = (event: { target: { value: string } }) => {
    setSearch(event.target.value)
    setPageNumber(1)
  }

  const handlePlaylistSelect = useCallback((playlist: Playlist) => {
    setSelectedPlaylist(playlist)
    setMediaPageNumber(1)
    loadPlaylistMedia(playlist.id, 1)
  }, [loadPlaylistMedia])

  const handleReturnToPlaylists = useCallback(() => {
    setSelectedPlaylist(null)
    setPlaylistMedia([])
  }, [])

  const handlePlaylistCreated = useCallback(() => {
    reloadPlaylists()
  }, [reloadPlaylists])

  const handleMediaRemoved = useCallback(() => {
    if (selectedPlaylist) {
      loadPlaylistMedia(selectedPlaylist.id, mediaPageNumber)
      reloadPlaylists() // Refresh stats
    }
  }, [selectedPlaylist, mediaPageNumber, loadPlaylistMedia, reloadPlaylists])

  /** Same availability guard as starting playback: no file, nothing to clip. */
  const handleClip = useCallback((track: Download) => {
    if (!track.file_path) {
      toast.error("Media file not available")
      return
    }
    if (track.media_type !== "AUDIO" && track.media_type !== "VIDEO") return
    setClipTarget(track)
  }, [])

  const handlePlaylistDeleted = useCallback(() => {
    setSelectedPlaylist(null)
    setPlaylistMedia([])
    reloadPlaylists()
  }, [reloadPlaylists])

  // Both actions decide something on the server's behalf — sharing, and which
  // collections to keep in sync — so offline mode shows the list bare.
  const playlistActions: ActionDescriptor<Playlist>[] = offlineMode ? [] : [
    {
      key: "offlineSync",
      title: "Keep offline",
      icon: ArrowPathIcon,
      render: (playlist) => <CollectionOfflineButton source={playlistSource(playlist.id)} />,
    },
    {
      key: "share",
      title: "Share",
      icon: UserPlusIcon,
      onClick: (playlist) => {
        if (playlist.user_id !== user?.id && !user?.is_admin) {
          toast.error("Only the owner can manage sharing")
          return
        }
        setShareTarget(playlist)
      },
      buttonClassName: "hover:bg-matrix/20",
      iconClassName: "text-text-muted hover:text-matrix",
    },
  ]

  const playlistColumns = buildPlaylistColumns({
    actionsLabel: actionsHeaderStrip(playlistActions),
    renderActions: (playlist) => (
      <ActionList actions={playlistActions} row={playlist} />
    ),
  })


  // Clip editor. Ahead of the video branch so it wins over an inline player left
  // visible by whatever was playing when the scissors were clicked.
  if (clipTarget) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <Card className="mt-4">
          <CardContent className="pt-6">
            <MediaClipEditor
              media={clipTarget}
              onBack={() => setClipTarget(null)}
              backLabel={selectedPlaylist ? "Back to Playlist" : "Back"}
            />
          </CardContent>
        </Card>
      </motion.div>
    )
  }

  // Video playback view (inline). Works for both a real playlist and a Tag Mix,
  // since InlinePlaylistVideoPlayer reads purely from MediaPlayerContext.
  if (displayVideo) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <Card className="mt-4">
          <CardContent className="pt-3 md:pt-4">
            <InlinePlaylistVideoPlayer
              onReturn={() => setDisplayVideo(false)}
            />
          </CardContent>
        </Card>
      </motion.div>
    )
  }

  if (selectedPlaylist) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <Card className="mt-4">
          <CardHeader className="pb-3">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
              <div className="flex items-center gap-3">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleReturnToPlaylists}
                  className="gap-2"
                >
                  <ArrowLeftIcon className="h-4 w-4" />
                  Back
                </Button>
                <div>
                  <CardTitle className="text-lg">{selectedPlaylist.name}</CardTitle>
                  {selectedPlaylist.description && (
                    <p className="text-sm text-text-muted mt-1">{selectedPlaylist.description}</p>
                  )}
                </div>
              </div>
              <div className="flex gap-4 text-sm font-mono">
                <span className="text-text-secondary">
                  Items: <span className="text-matrix">{selectedPlaylist.media_count}</span>
                </span>
                <span className="text-text-secondary">
                  Duration: <span className="text-text-primary">{formatDuration(selectedPlaylist.total_duration)}</span>
                </span>
              </div>
            </div>
          </CardHeader>

          <CardContent className="space-y-4">
            <PlaylistDetailView
              playlistId={selectedPlaylist.id}
              playlistName={selectedPlaylist.name}
              tracks={playlistMedia}
              loading={mediaLoading}
              totalTracks={totalTracks}
              sortBy={trackSortBy}
              sortDirection={trackSortDirection}
              onSort={handleTrackSort}
              onTracksChanged={handleMediaRemoved}
              onPlaylistDeleted={handlePlaylistDeleted}
              onVideoPlayback={() => setDisplayVideo(true)}
              onClip={handleClip}
              patchRow={patchTrack}
              reorderTracks={reorderTracks}
              restoreTracks={restoreTracks}
            />

            <TablePagination
              pageNumber={mediaPageNumber}
              pageCount={mediaPageCount}
              setPageNumber={(page) => {
                setMediaPageNumber(page)
                loadPlaylistMedia(selectedPlaylist.id, page)
              }}
            />
          </CardContent>
        </Card>
      </motion.div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <Card className="mt-4">
        <CardHeader className="pb-3">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            {offlineMode ? (
              <CardTitle className="font-mono text-sm text-matrix">Playlists</CardTitle>
            ) : (
              <div className="inline-flex rounded-md border border-border overflow-hidden font-mono text-sm self-start">
                <button
                  onClick={() => setMode('playlists')}
                  className={cn(
                    "px-3 py-1.5 transition-colors",
                    mode === 'playlists'
                      ? "bg-matrix/20 text-matrix"
                      : "text-text-muted hover:text-text-secondary"
                  )}
                >
                  Playlists
                </button>
                <button
                  onClick={() => setMode('tagmix')}
                  className={cn(
                    "px-3 py-1.5 border-l border-border transition-colors",
                    mode === 'tagmix'
                      ? "bg-matrix/20 text-matrix"
                      : "text-text-muted hover:text-text-secondary"
                  )}
                >
                  Tag Mix
                </button>
              </div>
            )}
            {mode === 'playlists' && (
              <PlaylistStatsBar totalPlaylists={totalPlaylists} loading={loading && tableRows.length === 0} />
            )}
          </div>
        </CardHeader>

        <CardContent className="space-y-4">
          {mode === 'playlists' ? (
            <>
              {/* Search and Create */}
              <div className="flex flex-col sm:flex-row gap-3">
                <div className="relative flex-1 max-w-md">
                  <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-text-muted" />
                  <Input
                    placeholder="Search playlists..."
                    value={search}
                    onChange={handleInputChange}
                    className="pl-9"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <ViewToggle mode={listViewMode} onChange={setListViewMode} />
                  {!offlineMode && (
                    <Button onClick={() => setCreateDialogOpen(true)} className="gap-2" title="Create Playlist">
                      <PlusIcon className="h-4 w-4" />
                      <span className="hidden sm:inline">Create Playlist</span>
                    </Button>
                  )}
                </div>
              </div>

              {/* Playlists, as a table or a grid of collage cards */}
              <div
                className={
                  listViewMode === "table"
                    ? "rounded-lg border border-border overflow-hidden"
                    : ""
                }
              >
                {listViewMode === "grid" ? (
                  <CardGrid
                    rows={tableRows}
                    loading={loading}
                    emptyMessage="No playlists found"
                    getRowKey={(playlist) => playlist.id}
                    renderCard={(playlist, index) => (
                      <PlaylistCard
                        playlist={playlist}
                        index={index}
                        onClick={() => handlePlaylistSelect(playlist)}
                        actions={
                          <ActionList actions={playlistActions} row={playlist} />
                        }
                      />
                    )}
                  />
                ) : (
                  <DataTable
                    columns={playlistColumns}
                    rows={tableRows}
                    loading={loading}
                    emptyMessage="No playlists found"
                    getRowKey={(playlist) => playlist.id}
                    onRowClick={handlePlaylistSelect}
                    sortBy={sortBy}
                    sortDirection={sortDirection}
                    onSort={handleSort}
                    sortOptions={PLAYLIST_SORT_OPTIONS}
                    renderActions={(playlist) => (
                      <ActionList actions={playlistActions} row={playlist} />
                    )}
                  />
                )}
              </div>

              <TablePagination
                pageNumber={pageNumber}
                pageCount={pageCount}
                setPageNumber={setPageNumber}
              />
            </>
          ) : (
            <TagMixView
              selectedTagIds={mixTagIds}
              onChangeTags={setMixTagIds}
              onVideoPlayback={() => setDisplayVideo(true)}
              onClip={handleClip}
            />
          )}
        </CardContent>
      </Card>

      {shareTarget && (
        <ShareDialog
          open
          onOpenChange={(open) => !open && setShareTarget(null)}
          entityIds={[shareTarget.id]}
          entityType="playlists"
          entityTitle={shareTarget.name}
        />
      )}

      <CreatePlaylistDialog
        open={createDialogOpen}
        onOpenChange={setCreateDialogOpen}
        onPlaylistCreated={handlePlaylistCreated}
      />
    </motion.div>
  )
}
