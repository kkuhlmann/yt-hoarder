"use client"

import { useState } from "react"
import type { ReactNode } from "react"
import type { TranscriptStorage } from "@/app/types/StatsOptions"
import { formatBytes } from "@/app/utils"
import { RankedBarList } from "./RankedBarList"
import { StatSummaryCard } from "./StatSummaryCard"
import { formatCount, formatShare } from "./format"

const TOP_CHANNELS = 10

interface TranscriptStorageBreakdownProps {
  storage: TranscriptStorage | null
  loading: boolean
  failed: boolean
  color: string
}

function counted(n: number, noun: string): string {
  return `${formatCount(n)} ${noun}${n === 1 ? "" : "s"}`
}

function Note({ children }: { children: ReactNode }) {
  return <p className="text-xs font-mono text-text-muted">{children}</p>
}

export function TranscriptStorageBreakdown({ storage, loading, failed, color }: TranscriptStorageBreakdownProps) {
  const [showAll, setShowAll] = useState(false)

  if (failed && !loading) return <Note>Couldn&apos;t measure database storage</Note>
  // A refetch keeps showing the previous figures; only a first load or a retry
  // after a failure has nothing better to show.
  if (!storage || failed) return loading ? <Note>Measuring database storage…</Note> : null
  if (storage.total_bytes <= 0) return null

  const channels = showAll ? storage.by_channel : storage.by_channel.slice(0, TOP_CHANNELS)

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm text-text-secondary font-mono mb-2">Database Storage</p>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          <div className="col-span-2 sm:col-span-1">
            <StatSummaryCard
              label="Transcripts"
              value={formatBytes(storage.total_bytes)}
              sub={
                storage.database_bytes != null
                  ? `${formatShare(storage.total_bytes, storage.database_bytes)} of ${formatBytes(storage.database_bytes)} database`
                  : undefined
              }
            />
          </div>
          <StatSummaryCard
            label="Text & keyword index"
            value={formatBytes(storage.text_bytes)}
            sub={`${formatShare(storage.text_bytes, storage.total_bytes)} of transcript storage`}
          />
          <StatSummaryCard
            label="Embeddings & vector index"
            value={formatBytes(storage.embedding_bytes)}
            sub={`${formatShare(storage.embedding_bytes, storage.total_bytes)} of transcript storage`}
          />
        </div>
      </div>
      {storage.by_channel.length > 1 && (
        <div className="min-w-0">
          <p className="text-sm text-text-secondary font-mono mb-2">Storage by Channel</p>
          <RankedBarList
            items={channels.map((c) => ({
              label: c.channel,
              value: c.total_bytes,
              sub: `${counted(c.transcript_count, "transcript")} · ${counted(c.block_count, "block")}`,
            }))}
            color={color}
            formatValue={formatBytes}
          />
          {storage.by_channel.length > TOP_CHANNELS && (
            <button
              type="button"
              aria-expanded={showAll}
              onClick={() => setShowAll((v) => !v)}
              className="mt-2 px-3 py-1 text-xs font-mono rounded text-matrix hover:bg-matrix/10 transition-colors"
            >
              {showAll ? `Show top ${TOP_CHANNELS}` : `Show all ${storage.by_channel.length} channels`}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
