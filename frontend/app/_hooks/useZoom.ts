import { useState, useCallback, useMemo } from 'react'

interface UseZoomProps {
  duration: number
  selectionStart: number
  selectionEnd: number
  minZoom?: number // 1 = full view
  maxZoom?: number // e.g., 20 = 20x zoom
  /**
   * Re-centre on the selection with every zoom step, rather than zooming about the
   * current centre. For a surface with no waveform, where the slider is the only view and
   * a selection zoomed out of it leaves both handles pinned to an edge. Only a zoom step
   * moves the view, never a change to the selection, so a dragged handle can't drag it.
   */
  anchorOnSelection?: boolean
}

interface UseZoomReturn {
  zoomLevel: number
  viewStart: number
  viewEnd: number
  zoomIn: () => void
  zoomOut: () => void
  zoomToSelection: () => void
  resetZoom: () => void
  isZoomed: boolean
}

const ZOOM_STEPS = [1, 1.5, 2, 3, 4, 6, 8, 12, 16, 20]

export function useZoom({
  duration,
  selectionStart,
  selectionEnd,
  minZoom = 1,
  maxZoom = 20,
  anchorOnSelection = false,
}: UseZoomProps): UseZoomReturn {
  const [zoomLevel, setZoomLevel] = useState(1)
  // Null means "centred". Seeding from `duration` here instead would capture the
  // mount-time value — 0 for a row with no stored duration — and pin every zoom
  // level to t=0 for good, since only zoomToSelection/resetZoom ever write it.
  const [viewportCenter, setViewportCenter] = useState<number | null>(null)

  const { viewStart, viewEnd } = useMemo(() => {
    if (!Number.isFinite(duration) || duration <= 0) {
      return { viewStart: 0, viewEnd: 0 }
    }

    const center = viewportCenter ?? duration / 2
    const viewportDuration = duration / zoomLevel
    let start = center - viewportDuration / 2
    let end = center + viewportDuration / 2

    if (start < 0) {
      start = 0
      end = Math.min(duration, viewportDuration)
    }
    if (end > duration) {
      end = duration
      start = Math.max(0, duration - viewportDuration)
    }

    return { viewStart: start, viewEnd: end }
  }, [duration, zoomLevel, viewportCenter])

  const isZoomed = zoomLevel > 1

  const anchorToSelection = useCallback(() => {
    if (anchorOnSelection && selectionEnd > selectionStart) {
      setViewportCenter((selectionStart + selectionEnd) / 2)
    }
  }, [anchorOnSelection, selectionStart, selectionEnd])

  const zoomIn = useCallback(() => {
    setZoomLevel((current) => {
      const nextStep = ZOOM_STEPS.find((step) => step > current)
      return nextStep ? Math.min(nextStep, maxZoom) : current
    })
    anchorToSelection()
  }, [maxZoom, anchorToSelection])

  const zoomOut = useCallback(() => {
    setZoomLevel((current) => {
      const steps = [...ZOOM_STEPS].reverse()
      const nextStep = steps.find((step) => step < current)
      return nextStep ? Math.max(nextStep, minZoom) : current
    })
    anchorToSelection()
  }, [minZoom, anchorToSelection])

  const zoomToSelection = useCallback(() => {
    if (!Number.isFinite(duration) || duration <= 0) return
    if (selectionEnd <= selectionStart) return

    const selectionDuration = selectionEnd - selectionStart
    const selectionCenter = (selectionStart + selectionEnd) / 2

    // Add 20% padding on each side
    const paddedDuration = selectionDuration * 1.4
    const targetZoom = duration / paddedDuration

    // Find the closest zoom step that shows the selection
    const appropriateZoom = ZOOM_STEPS.reduce((closest, step) => {
      if (step <= targetZoom && step <= maxZoom) {
        return step
      }
      return closest
    }, 1)

    setZoomLevel(appropriateZoom)
    setViewportCenter(selectionCenter)
  }, [duration, selectionStart, selectionEnd, maxZoom])

  const resetZoom = useCallback(() => {
    setZoomLevel(1)
    setViewportCenter(null)
  }, [])

  return {
    zoomLevel,
    viewStart,
    viewEnd,
    zoomIn,
    zoomOut,
    zoomToSelection,
    resetZoom,
    isZoomed,
  }
}
