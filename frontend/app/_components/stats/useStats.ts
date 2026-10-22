"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import { apiUrl } from "@/app/lib/api"
import axios from "axios"
import { useAdmin } from "@/app/context/AdminContext"
import { useFetchEffect } from "@/app/_hooks/useFetchEffect"
import type {
  LibraryOverview,
  StorageStats,
  DownloadsOverTime,
  TranscriptionStats,
  TranscriptStorage,
  EngagementStats,
  ClipsStats,
  DownloadSuccessRate,
  DownloadActivityHeatmap,
  Granularity,
  StatsFilter,
} from "@/app/types/StatsOptions"

// Owns all stats-page data fetching: the endpoint responses, the loading/error
// state, and the filter/granularity params that drive refetches.
export function useStats() {
  const [overview, setOverview] = useState<LibraryOverview | null>(null)
  const [storage, setStorage] = useState<StorageStats | null>(null)
  const [downloads, setDownloads] = useState<DownloadsOverTime | null>(null)
  const [transcription, setTranscription] = useState<TranscriptionStats | null>(null)
  const [engagement, setEngagement] = useState<EngagementStats | null>(null)
  const [clips, setClips] = useState<ClipsStats | null>(null)
  const [successRate, setSuccessRate] = useState<DownloadSuccessRate | null>(null)
  const [heatmap, setHeatmap] = useState<DownloadActivityHeatmap | null>(null)
  const [transcriptStorage, setTranscriptStorage] = useState<TranscriptStorage | null>(null)
  const [transcriptStorageFailed, setTranscriptStorageFailed] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [granularity, setGranularity] = useState<Granularity>("day")
  const [filter, setFilter] = useState<StatsFilter>(null)
  const { adminParam } = useAdmin()

  const getFilterParams = useCallback(() => {
    const base = { ...adminParam }
    if (!filter) return base
    if (filter.type === "channel") return { ...base, channel: filter.channel }
    return { ...base, playlist_id: filter.playlist_id }
  }, [filter, adminParam])

  const isInitialMount = useRef(true)
  useEffect(() => {
    const fetchAll = async () => {
      try {
        // Only show full loading spinner on initial mount, not filter changes
        if (isInitialMount.current) setLoading(true)
        const fp = getFilterParams()
        const [ov, st, dl, tr, en, cl, sr, hm] = await Promise.all([
          axios.get(apiUrl("/stats/overview"), { params: fp }),
          axios.get(apiUrl("/stats/storage"), { params: fp }),
          axios.get(apiUrl("/stats/downloads-over-time"), { params: { granularity, ...fp } }),
          axios.get(apiUrl("/stats/transcription"), { params: fp }),
          axios.get(apiUrl("/stats/engagement"), { params: fp }),
          axios.get(apiUrl("/stats/clips"), { params: { granularity, ...fp } }),
          axios.get(apiUrl("/stats/download-success-rate"), { params: { granularity, ...fp } }),
          axios.get(apiUrl("/stats/download-activity-heatmap"), { params: fp }),
        ])
        setOverview(ov.data)
        setStorage(st.data)
        setDownloads(dl.data)
        setTranscription(tr.data)
        setEngagement(en.data)
        setClips(cl.data)
        setSuccessRate(sr.data)
        setHeatmap(hm.data)
      } catch (err) {
        console.error("Failed to fetch stats:", err)
        setError("Failed to load statistics")
      } finally {
        setLoading(false)
        isInitialMount.current = false
      }
    }
    fetchAll()
  }, [filter, adminParam]) // eslint-disable-line react-hooks/exhaustive-deps

  // Kept out of the Promise.all above: once the server's snapshot expires, the next
  // request scans every transcript row, and the rest of the page shouldn't wait on it.
  const loadTranscriptStorage = useCallback(
    (signal: AbortSignal) =>
      axios
        .get(apiUrl("/stats/transcript-storage"), { params: getFilterParams(), signal })
        .then((res) => {
          setTranscriptStorage(res.data)
          setTranscriptStorageFailed(false)
        })
        .catch((err) => {
          if (axios.isCancel(err)) return
          console.error("Failed to fetch transcript storage:", err)
          setTranscriptStorageFailed(true)
        }),
    [getFilterParams]
  )
  const { isLoading: transcriptStorageLoading } = useFetchEffect(
    loadTranscriptStorage,
    [loadTranscriptStorage],
    { initialLoading: true }
  )

  // Refetch only time-series data when granularity changes (skip initial render).
  // A ref, not state: nothing renders it, and as state it made the skip itself a
  // synchronous setState inside the effect.
  const skipFirstGranularityFetch = useRef(true)
  useEffect(() => {
    if (skipFirstGranularityFetch.current) {
      skipFirstGranularityFetch.current = false
      return
    }
    const fetchTimeSeries = async () => {
      try {
        const fp = getFilterParams()
        const [dl, cl, sr] = await Promise.all([
          axios.get(apiUrl("/stats/downloads-over-time"), { params: { granularity, ...fp } }),
          axios.get(apiUrl("/stats/clips"), { params: { granularity, ...fp } }),
          axios.get(apiUrl("/stats/download-success-rate"), { params: { granularity, ...fp } }),
        ])
        setDownloads(dl.data)
        setClips(cl.data)
        setSuccessRate(sr.data)
      } catch (err) {
        console.error("Failed to fetch time series stats:", err)
      }
    }
    fetchTimeSeries()
  }, [granularity, adminParam]) // eslint-disable-line react-hooks/exhaustive-deps

  return {
    overview,
    storage,
    downloads,
    transcription,
    engagement,
    clips,
    successRate,
    heatmap,
    transcriptStorage,
    transcriptStorageLoading,
    transcriptStorageFailed,
    loading,
    error,
    granularity,
    setGranularity,
    filter,
    setFilter,
  }
}
