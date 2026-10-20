"use client"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { LoadingTable } from "@/app/_components/LoadingSpinner"
import { Dispatch, ReactNode, SetStateAction, type JSX } from "react"
import { useMediaPlayer } from "@/app/context/MediaPlayerContext"
import { useDelayedFlag } from "@/app/_hooks/useDelayedFlag"
import { TranscriptSegment } from "../types/TranscriptSegments"
import { motion } from "framer-motion"
import { DocumentTextIcon } from "@heroicons/react/20/solid"
import { formatTime } from "@/app/utils"

type TranscriptSegmentTableProps = {
  tableColumns: (string | JSX.Element)[]
  tableRows: TranscriptSegment[]
  loading: boolean
  setRowSelect: Dispatch<SetStateAction<any>>
  setDisplayVideo: (visible: boolean) => void
  /**
   * Opens the page for a hit with no file to play (transcript-only, or deleted with its
   * transcript kept), which plays it through YouTube beside the transcript.
   */
  onOpenTranscript: (mediaId: number, seconds: number) => void
  searchQuery?: string
}

// PostgreSQL's default English stopword list (src/backend/snowball/stopwords/english.stop)
// These words are stripped from full-text search queries by PostgreSQL, so highlighting
// them in the frontend creates misleading visual noise.
const ENGLISH_STOPWORDS = new Set([
  "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your", "yours",
  "yourself", "yourselves", "he", "him", "his", "himself", "she", "her", "hers", "herself",
  "it", "its", "itself", "they", "them", "their", "theirs", "themselves", "what", "which",
  "who", "whom", "this", "that", "these", "those", "am", "is", "are", "was", "were", "be",
  "been", "being", "have", "has", "had", "having", "do", "does", "did", "doing", "a", "an",
  "the", "and", "but", "if", "or", "because", "as", "until", "while", "of", "at", "by",
  "for", "with", "about", "against", "between", "into", "through", "during", "before",
  "after", "above", "below", "to", "from", "up", "down", "in", "out", "on", "off", "over",
  "under", "again", "further", "then", "once", "here", "there", "when", "where", "why",
  "how", "all", "any", "both", "each", "few", "more", "most", "other", "some", "such", "no",
  "nor", "not", "only", "own", "same", "so", "than", "too", "very", "s", "t", "can", "will",
  "just", "don", "should", "now",
])

function highlightMatches(text: string, query: string): ReactNode {
  if (!query || query.length < 3) return text

  const words = query
    .split(/\s+/)
    .filter((w) => w.length >= 2)
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .filter((w) => !ENGLISH_STOPWORDS.has(w.toLowerCase()))

  if (words.length === 0) return text

  const pattern = new RegExp(`(${words.join("|")})`, "gi")
  const parts = text.split(pattern)

  return parts.map((part, i) =>
    pattern.test(part) ? (
      <span key={i} className="bg-matrix/20 text-matrix font-semibold rounded px-0.5">
        {part}
      </span>
    ) : (
      part
    )
  )
}

export function TranscriptSegmentTable({
  tableColumns,
  tableRows,
  loading,
  setRowSelect,
  setDisplayVideo,
  onOpenTranscript,
  searchQuery = "",
}: TranscriptSegmentTableProps) {
  // Spinner only after 500ms, so a fast refetch doesn't flash one.
  const delayedLoading = useDelayedFlag(loading)
  const { audioPlayer, openAudioPlayer, closeAudioPlayer } = useMediaPlayer()

  const handleRowClick = (row: TranscriptSegment) => {
    if (row.media_details.media_type === "TRANSCRIPT" || row.media_details.status === "DELETED") {
      onOpenTranscript(row.media_details_id, row.start_time)
      return
    }

    setRowSelect({
      media_details_id: row.media_details_id,
      title: row.media_details.title,
      channel: row.media_details.channel,
      media_type: row.media_details.media_type,
      url: row.media_details.url,
      start_time: row.start_time,
      duration: row.media_details.duration || 0,
      thumbnail_path: row.media_details.thumbnail_path,
      playback_position: row.start_time,
      // The timestamp is the whole point of the click, so it stands even when it
      // lands in the final seconds of the video.
      exact_start: true,
    })
    if (row.media_details.media_type === "VIDEO") {
      closeAudioPlayer()
      setDisplayVideo(true)
    } else if (row.media_details.media_type === "AUDIO") {
      setDisplayVideo(false)
      openAudioPlayer({
        ...audioPlayer,
        media_details_id: row.media_details_id,
        title: row.media_details.title,
        channel: row.media_details.channel,
        start_time: row.start_time,
        exact_start: true,
        duration: row.media_details.duration,
        thumbnail_path: row.media_details.thumbnail_path,
      })
    }
  }

  const TableRow = (row: TranscriptSegment, index: number) => {
    const isDeleted = row.media_details.status === "DELETED"
    const isTranscriptOnly = row.media_details.media_type === "TRANSCRIPT"
    return (
      <motion.tr
        key={row.transcript_block_id}
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2, delay: index * 0.02 }}
        onClick={() => handleRowClick(row)}
        className={cn(
          "cursor-pointer hover:bg-bg-surface/50 transition-colors duration-200",
          isDeleted && "opacity-75"
        )}
      >
        {/* Similarity */}
        <td className="py-1 px-1.5 md:p-3">
          <Badge
            variant={
              row.similarity > 0.55
                ? "success"
                : row.similarity > 0.4
                  ? "info"
                  : "secondary"
            }
            className="font-mono"
          >
            {(row.similarity * 100).toFixed(0)}%
          </Badge>
        </td>

        {/* FTS Rank - hidden on mobile */}
        <td className="hidden md:table-cell p-2 md:p-3">
          {row.fts_rank != null ? (
            <span className="text-xs md:text-sm font-mono text-text-secondary">
              {row.fts_rank.toFixed(3)}
            </span>
          ) : (
            <span className="text-xs md:text-sm text-text-muted">-</span>
          )}
        </td>

        {/* Text */}
        <td className="py-1 px-1.5 md:p-3 max-w-xs md:max-w-lg">
          <span className="text-xs md:text-sm text-text-primary leading-tight line-clamp-3 md:line-clamp-none">
            {highlightMatches(row.text, searchQuery)}
          </span>
        </td>

        {/* Channel */}
        <td className="hidden md:table-cell py-1 px-1.5 md:p-3">
          <span className="text-xs md:text-sm text-text-secondary">
            {row.media_details.channel}
          </span>
        </td>

        {/* Title */}
        <td className="hidden lg:table-cell py-1 px-1.5 md:p-3 max-w-[200px]">
          <span
            className="text-xs md:text-sm text-text-secondary truncate block"
            title={row.media_details.title}
          >
            {(isDeleted || isTranscriptOnly) && (
              <DocumentTextIcon
                className="h-3.5 w-3.5 inline mr-1 text-text-muted"
                title={isDeleted ? "Deleted: plays from the source" : "Transcript only: plays from the source"}
              />
            )}
            {row.media_details.title}
          </span>
        </td>

        {/* Start Time */}
        <td className="py-1 px-1.5 md:p-3">
          <span className={cn(
            "text-xs md:text-sm font-mono",
            isDeleted ? "text-status-warning" : "text-matrix"
          )}>
            {formatTime(row.start_time)}
          </span>
        </td>
      </motion.tr>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left">
        <thead>
          <tr className="border-b border-border bg-bg-surface">
            {tableColumns.map((head: any, idx: number) => (
              <th
                key={idx}
                className={cn(
                  "p-2 md:p-3 font-mono text-xs text-text-secondary uppercase tracking-wider",
                  idx === 1 && "hidden md:table-cell",
                  idx === 3 && "hidden md:table-cell",
                  idx === 4 && "hidden lg:table-cell"
                )}
              >
                {head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {delayedLoading ? (
            <LoadingTable length={tableColumns.length} />
          ) : tableRows.length === 0 ? (
            <tr>
              <td
                colSpan={tableColumns.length}
                className="p-8 text-center text-text-muted font-mono"
              >
                No transcript segments found above similarity threshold
              </td>
            </tr>
          ) : (
            tableRows.map(TableRow)
          )}
        </tbody>
      </table>
    </div>
  )
}
