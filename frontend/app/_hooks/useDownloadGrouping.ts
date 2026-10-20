"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import axios from "axios"
import { apiUrl } from "@/app/lib/api"
import { mediaApi } from "@/app/lib/mediaApi"
import { TRANSCRIPTS_SCOPE, scopeParams } from "@/app/lib/mediaScope"
import { useFetchEffect } from "@/app/_hooks/useFetchEffect"
import { useAdmin } from "@/app/context/AdminContext"
import type { GroupDim, GroupLeafFilter, MediaGroup } from "@/app/types/DownloadsOptions"

const FOLDER_PAGE_SIZE = 60

export const GROUP_DIM_LABELS: Record<GroupDim, string> = {
  channel: "Channel",
  tag: "Tags",
  downloaded: "Download date",
  released: "Upload date",
}

/**
 * GROUP_DIM_LABELS as a scope shows them. In the Transcripts view `downloaded_at` is
 * when an item was added, since a transcript-only item never downloaded any media.
 */
export function groupDimLabels(scope: string): Record<GroupDim, string> {
  return scope === TRANSCRIPTS_SCOPE
    ? { ...GROUP_DIM_LABELS, downloaded: "Added date" }
    : GROUP_DIM_LABELS
}

type Segment = { key: string; label: string }

// What the leaf (media list) needs: an optional tag-id override plus list filters.
type GroupLeaf = { tagIds?: number[]; filter: GroupLeafFilter }

type Args = {
  enabled: boolean
  /** The list's scope: a status, or TRANSCRIPTS_SCOPE. */
  status: string
  search: string
  tagIds: number[]
  minRating: number | null
}

function isDateDim(dim: GroupDim | null): boolean {
  return dim === "downloaded" || dim === "released"
}

export function useDownloadGrouping({ enabled, status, search, tagIds, minRating }: Args) {
  const { adminParam } = useAdmin()

  const [groupDim, setGroupDimState] = useState<GroupDim | null>(null)
  const [groupPath, setGroupPath] = useState<Segment[]>([])
  const [folderPage, setFolderPage] = useState(1)

  const [folders, setFolders] = useState<MediaGroup[]>([])
  const [foldersPageCount, setFoldersPageCount] = useState(0)

  const isGrouping = groupDim !== null
  const atLeaf = useMemo(() => {
    if (!groupDim) return false
    return isDateDim(groupDim) ? groupPath.length >= 2 : groupPath.length >= 1
  }, [groupDim, groupPath])
  const showFolders = isGrouping && !atLeaf

  // Reset grouping entirely (e.g. when grid view is left or status changes)
  const reset = useCallback(() => {
    setGroupDimState(null)
    setGroupPath([])
    setFolderPage(1)
  }, [])

  const setGroupDim = useCallback((dim: GroupDim | null) => {
    setGroupDimState(dim)
    setGroupPath([])
    setFolderPage(1)
  }, [])

  const openFolder = useCallback((group: MediaGroup) => {
    setGroupPath((path) => [...path, { key: group.key, label: group.label }])
    setFolderPage(1)
  }, [])

  const goUp = useCallback(() => {
    setGroupPath((path) => path.slice(0, -1))
    setFolderPage(1)
  }, [])

  // Grouping is a mode: leaving it discards the drill-down, so re-entering starts
  // at the root. Done on teardown rather than in the effect body — same
  // transition, but it keeps the reset out of react-hooks/set-state-in-effect.
  useEffect(() => {
    if (!enabled) return
    return reset
  }, [enabled, reset])

  // What the open path selects, at whatever depth it has reached — a year on its own
  // is a scope, not just a waypoint. The media list only ever wants the leaf; the
  // transcript search scopes to any depth.
  const scope: GroupLeaf | null = useMemo(() => {
    if (!groupDim || groupPath.length === 0) return null
    if (groupDim === "channel") {
      return { filter: { channel: groupPath[0].key } }
    }
    if (groupDim === "tag") {
      const key = groupPath[0].key
      if (key === "untagged") return { tagIds: [], filter: { untagged: true } }
      return { tagIds: [Number(key)], filter: {} }
    }
    // date dims: groupPath[0] = year, groupPath[1] = "YYYY-MM"
    const year = Number(groupPath[0].key)
    const month = groupPath[1] ? Number(groupPath[1].key.split("-")[1]) : undefined
    return { filter: { dateField: groupDim, year, month } }
  }, [groupDim, groupPath])

  // Stable id so consumers can react to scope changes.
  const scopeKey = useMemo(
    () =>
      groupDim && groupPath.length > 0
        ? `${groupDim}:${groupPath.map((s) => s.key).join("/")}`
        : null,
    [groupDim, groupPath]
  )

  // The leaf is the scope at full depth. `leafKey` keys the media list's page number
  // and the playback queue pool, so it must stay null above the leaf.
  const leaf = atLeaf ? scope : null
  const leafKey = atLeaf ? scopeKey : null

  const breadcrumb = useMemo(
    () => ({
      dimLabel: groupDim ? groupDimLabels(status)[groupDim] : "",
      segments: groupPath,
    }),
    [groupDim, groupPath, status]
  )

  // Fetch folders whenever we're showing them (and the filter context changes).
  const effectiveSearch = search.length > 2 ? search : ""

  const loadFolders = useCallback(
    (signal: AbortSignal) => {
      if (!groupDim) return
      const params: Record<string, any> = {
        group_by: groupDim,
        ...scopeParams(status),
        page: folderPage,
        page_size: FOLDER_PAGE_SIZE,
        ...adminParam,
      }
      if (effectiveSearch) params.search = effectiveSearch
      if (tagIds.length > 0) params.tag_ids = tagIds.join(",")
      if (minRating != null) params.min_rating = minRating
      if (isDateDim(groupDim)) {
        params.level = groupPath.length === 0 ? "year" : "month"
        if (params.level === "month") params.parent = groupPath[0].key
      }

      return axios
        .get(apiUrl(mediaApi.groups), { params, signal })
        .then((res) => {
          setFolders(res.data.groups ?? [])
          setFoldersPageCount(res.data.page_count ?? 0)
        })
        .catch((err) => {
          // Staleness is now handled by the abort in useFetchEffect rather than
          // a request-id guard. Without this check an aborted request would
          // blank the folder list that the superseding one is about to fill.
          if (axios.isCancel(err)) return
          setFolders([])
          setFoldersPageCount(0)
        })
    },
    [
      groupDim,
      groupPath,
      status,
      effectiveSearch,
      tagIds,
      minRating,
      folderPage,
      adminParam,
    ]
  )

  const { isLoading: foldersLoading } = useFetchEffect(loadFolders, [loadFolders], {
    enabled: enabled && showFolders,
  })

  return {
    groupDim,
    setGroupDim,
    groupPath,
    isGrouping,
    atLeaf,
    showFolders,
    scope,
    scopeKey,
    leaf,
    leafKey,
    breadcrumb,
    openFolder,
    goUp,
    reset,
    folders,
    foldersLoading,
    foldersPageCount,
    folderPage,
    setFolderPage,
  }
}
