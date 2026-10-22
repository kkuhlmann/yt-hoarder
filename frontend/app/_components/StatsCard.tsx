"use client"

import { Card, CardContent } from "@/components/ui/card"
import { useIsMobile } from "@/app/_hooks/useIsMobile"
import { StatsFilterCombobox } from "./StatsFilterCombobox"
import { GranularityToggle } from "./stats/GranularityToggle"
import { StatsSectionNav, type NavSection } from "./stats/StatsSectionNav"
import { useStats } from "./stats/useStats"
import { useChartTheme } from "./stats/useChartTheme"
import { LibraryOverviewSection } from "./stats/LibraryOverviewSection"
import { StorageSection } from "./stats/StorageSection"
import { DownloadsOverTimeSection } from "./stats/DownloadsOverTimeSection"
import { ActivityHeatmapSection } from "./stats/ActivityHeatmapSection"
import { TranscriptionSection } from "./stats/TranscriptionSection"
import { ClipsSection } from "./stats/ClipsSection"
import { EngagementSection } from "./stats/EngagementSection"

const NAV_SECTIONS: NavSection[] = [
  { id: "overview", label: "Overview" },
  { id: "storage", label: "Storage" },
  { id: "downloads", label: "Downloads" },
  { id: "activity", label: "Activity" },
  { id: "transcription", label: "Transcription" },
  { id: "clips", label: "Clips" },
  { id: "engagement", label: "Engagement" },
]

export function StatsCard() {
  const {
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
  } = useStats()
  const { colors, tooltipStyle } = useChartTheme()
  const isMobile = useIsMobile()

  if (loading) {
    return (
      <Card className="bg-bg-terminal border-border h-full">
        <CardContent className="p-8 h-full flex items-center justify-center">
          <div className="flex items-center justify-center gap-3">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-matrix border-t-transparent" />
            <span className="font-mono text-text-secondary">Loading statistics...</span>
          </div>
        </CardContent>
      </Card>
    )
  }

  if (error) {
    return (
      <Card className="bg-bg-terminal border-border">
        <CardContent className="p-8 text-center">
          <p className="text-status-error font-mono">{error}</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="bg-bg-terminal border-border">
      <CardContent className="space-y-4">
        <div className="sticky top-[calc(3.5rem+env(safe-area-inset-top))] z-30 -mx-3 md:-mx-6 px-3 md:px-6 py-2 bg-bg-terminal/95 backdrop-blur-sm supports-backdrop-filter:bg-bg-terminal/70 border-b border-border rounded-t-lg">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
            <StatsFilterCombobox value={filter} onChange={setFilter} />
            <GranularityToggle value={granularity} onChange={setGranularity} />
          </div>
          <div className="mt-2">
            <StatsSectionNav sections={NAV_SECTIONS} />
          </div>
        </div>
        <LibraryOverviewSection overview={overview} />
        <StorageSection storage={storage} colors={colors} tooltipStyle={tooltipStyle} isMobile={isMobile} />
        <DownloadsOverTimeSection
          downloads={downloads}
          successRate={successRate}
          granularity={granularity}
          colors={colors}
          tooltipStyle={tooltipStyle}
          isMobile={isMobile}
        />
        <ActivityHeatmapSection heatmap={heatmap} isMobile={isMobile} />
        <TranscriptionSection
          transcription={transcription}
          storage={transcriptStorage}
          storageLoading={transcriptStorageLoading}
          storageFailed={transcriptStorageFailed}
          colors={colors}
        />
        <ClipsSection clips={clips} granularity={granularity} colors={colors} tooltipStyle={tooltipStyle} isMobile={isMobile} />
        <EngagementSection engagement={engagement} colors={colors} />
      </CardContent>
    </Card>
  )
}
