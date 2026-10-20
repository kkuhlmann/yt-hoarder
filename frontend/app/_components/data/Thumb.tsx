"use client"

import { useState } from "react"
import { DocumentTextIcon, FilmIcon, MusicalNoteIcon } from "@heroicons/react/20/solid"

import { apiUrl } from "@/app/lib/api"
import { mediaApi } from "@/app/lib/mediaApi"

export function ThumbPlaceholder({ mediaType }: { mediaType?: string }) {
  const Icon =
    mediaType === "AUDIO" ? MusicalNoteIcon : mediaType === "TRANSCRIPT" ? DocumentTextIcon : FilmIcon
  return (
    <div className="absolute inset-0 flex items-center justify-center bg-linear-to-br from-bg-surface to-bg-base">
      <Icon className="h-10 w-10 text-text-muted/40" />
    </div>
  )
}

/** One media thumbnail, falling back to a type-appropriate icon. */
export function Thumb({
  mediaId,
  alt,
  mediaType,
  className = "thumb-img absolute inset-0 w-full h-full object-cover",
}: {
  mediaId: number
  alt?: string
  mediaType?: string
  className?: string
}) {
  const [error, setError] = useState(false)
  if (error) return <ThumbPlaceholder mediaType={mediaType} />
  return (
    // eslint-disable-next-line @next/next/no-img-element -- thumbnails from local API
    <img
      src={apiUrl(mediaApi.thumbnail(mediaId))}
      alt={alt ?? ""}
      loading="lazy"
      onError={() => setError(true)}
      className={className}
    />
  )
}

function CollageCell({ mediaId }: { mediaId: number | undefined }) {
  const [error, setError] = useState(false)
  if (mediaId == null || error) {
    return (
      <div className="flex items-center justify-center bg-linear-to-br from-bg-surface to-bg-base">
        <FilmIcon className="h-5 w-5 text-text-muted/30" />
      </div>
    )
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- thumbnails from local API
    <img
      src={apiUrl(mediaApi.thumbnail(mediaId))}
      alt=""
      loading="lazy"
      onError={() => setError(true)}
      className="thumb-img w-full h-full object-cover"
    />
  )
}

/**
 * 2x2 collage of member thumbnails, for things that contain media (a playlist,
 * a group folder).
 *
 * Below two distinct ids it renders a single full-bleed thumbnail instead: a
 * 2x2 grid of the same image four times reads as a rendering bug, and
 * collections drawn from one channel hit that constantly.
 */
export function Collage({ mediaIds }: { mediaIds: number[] }) {
  const distinct = Array.from(new Set(mediaIds.filter((id) => id != null)))

  if (distinct.length === 0) {
    return <ThumbPlaceholder />
  }

  if (distinct.length === 1) {
    return <Thumb mediaId={distinct[0]} />
  }

  const cells = [0, 1, 2, 3].map((i) => mediaIds[i])
  return (
    <div className="absolute inset-0 grid grid-cols-2 grid-rows-2 gap-px">
      {cells.map((id, i) => (
        <CollageCell key={i} mediaId={id} />
      ))}
    </div>
  )
}
