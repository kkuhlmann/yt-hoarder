"use client"

import { CalendarIcon, EyeIcon } from "@heroicons/react/20/solid"
import { StarRating } from "@/app/_components/StarRating"
import { formatDate } from "@/app/utils"

/**
 * What sits under a player: title, channel, the viewer's rating, a source link, and the
 * upload date and play count. Shared by the library's video page and the transcript viewer.
 *
 * Each part renders only when given. `rating` undefined means not known yet (still
 * loading), as opposed to null, which is known to be unrated.
 */
export function MediaInfo({
  title,
  channel,
  url,
  urlLabel,
  releaseTimestamp,
  accessCount,
  rating,
  onRate,
}: {
  title: string
  channel?: string | null
  url?: string | null
  /** Shown in place of the raw URL. */
  urlLabel?: string
  releaseTimestamp?: string | null
  accessCount?: number
  rating?: number | null
  onRate?: (rating: number | null) => void
}) {
  return (
    <div className="text-center space-y-1">
      <h3 className="font-mono text-lg text-text-primary">{title}</h3>
      {channel && <p className="text-sm text-text-secondary">{channel}</p>}
      {rating !== undefined && (
        <div className="flex justify-center mt-0.5">
          <StarRating rating={rating} onRate={onRate} />
        </div>
      )}
      {url && (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-text-muted hover:text-matrix transition-colors font-mono truncate block"
        >
          {urlLabel ?? url}
        </a>
      )}
      {(releaseTimestamp || accessCount !== undefined) && (
        <div className="flex items-center justify-center gap-4 text-xs text-text-muted font-mono mt-1">
          {releaseTimestamp && (
            <span className="flex items-center gap-1">
              <CalendarIcon className="h-3 w-3" />
              {formatDate(releaseTimestamp)}
            </span>
          )}
          {accessCount !== undefined && (
            <span className="flex items-center gap-1">
              <EyeIcon className="h-3 w-3" />
              {accessCount} {accessCount === 1 ? "play" : "plays"}
            </span>
          )}
        </div>
      )}
    </div>
  )
}
