/**
 * Media API paths, in one place.
 *
 * A route rename is one edit. Add an entry here rather than hand-writing a path
 * at a call site.
 */

export const mediaApi = {
  // --- Collection ---
  list: "/media-details",
  stats: "/media-details/stats",
  groups: "/media-details/groups",
  semanticSearch: "/media-details/semantic/search",
  allTags: "/media-details/tags",

  // --- Single record ---
  detail: (id: number) => `/media-details/${id}`,
  hardDelete: (id: number) => `/media-details/${id}/hard`,
  playback: (id: number) => `/media-details/${id}/playback`,
  transcripts: (id: number) => `/media-details/${id}/transcripts`,
  transcript: (id: number) => `/media-details/${id}/transcript`,
  rating: (id: number) => `/media-details/${id}/rating`,
  tags: (id: number) => `/media-details/${id}/tags`,
  createTranscript: (id: number) => `/media-details/transcripts/${id}/create`,

  // --- Bulk ---
  bulkDelete: "/media-details/bulk-delete",
  bulkTags: "/media-details/bulk-tags",

  // --- Media streaming / assets (served by the /media router, not /media-details) ---
  stream: (id: number) => `/media/${id}`,
  thumbnail: (id: number) => `/media/${id}/thumbnail`,
  clip: (id: number) => `/media/clip/${id}`,
  peaks: (id: number) => `/media/${id}/peaks`,
  sprites: (id: number) => `/media/${id}/sprites`,
  spriteMetadata: (id: number) => `/media/${id}/sprites/metadata`,
} as const
