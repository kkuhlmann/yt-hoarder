import type { TranscriptionStats, TranscriptStorage } from "@/app/types/StatsOptions"
import type { ChartColors } from "./useChartTheme"
import { StatsPanel } from "./StatsPanel"
import { StatSummaryCard } from "./StatSummaryCard"
import { TranscriptStorageBreakdown } from "./TranscriptStorageBreakdown"
import { formatCount } from "./format"

interface TranscriptionSectionProps {
  transcription: TranscriptionStats | null
  storage: TranscriptStorage | null
  storageLoading: boolean
  storageFailed: boolean
  colors: ChartColors
}

export function TranscriptionSection({
  transcription,
  storage,
  storageLoading,
  storageFailed,
  colors,
}: TranscriptionSectionProps) {
  if (!transcription) return null

  return (
    <StatsPanel id="transcription" title="Transcription">
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <StatSummaryCard
            label="Coverage"
            value={`${transcription.coverage_percent}%`}
            sub={`${transcription.with_transcripts} of ${transcription.total_media} media`}
          />
          <StatSummaryCard label="Total Blocks" value={formatCount(transcription.total_blocks)} />
        </div>
        <TranscriptStorageBreakdown
          storage={storage}
          loading={storageLoading}
          failed={storageFailed}
          color={colors.blue}
        />
      </div>
    </StatsPanel>
  )
}
