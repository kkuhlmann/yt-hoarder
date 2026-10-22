"use client"

import { ArrowPathIcon, ScissorsIcon, XMarkIcon } from "@heroicons/react/24/solid"
import { Button } from "@/components/ui/button"
import { ZoomControls } from "@/app/_components/ZoomControls"
import { ClipSaveDialog } from "@/app/_components/clip/ClipSaveDialog"
import { ClipTrimControls } from "@/app/_components/clip/ClipTrimControls"
import type { ClipEditor, ClipOutputType } from "@/app/_hooks/useClipEditor"
import { useZoom } from "@/app/_hooks/useZoom"

type ClipPanelProps = {
  editor: ClipEditor
  onExit: () => void
  duration: number
  currentTime: number
  outputType: ClipOutputType
  onOutputTypeChange: (v: ClipOutputType) => void
}

/**
 * Clip controls for an item played through the YouTube embed, which the server cuts
 * from YouTube rather than from a file.
 *
 * The video page's controls minus the waveform, which needs a file to draw. The host
 * owns the editor, since it owns the embed the editor seeks and reads the position of.
 */
export function EmbedClipControls({
  clipping,
  onEnter,
  ...panel
}: ClipPanelProps & { clipping: boolean; onEnter: () => void }) {
  if (!clipping) {
    return (
      <div className="flex justify-center">
        <Button variant="outline" size="sm" onClick={onEnter} className="gap-2">
          <ScissorsIcon className="h-4 w-4" />
          Create Clip
        </Button>
      </div>
    )
  }
  return <ClipPanel {...panel} />
}

/** Mounted only while clipping, so every clip starts unzoomed. */
function ClipPanel({
  editor,
  onExit,
  duration,
  currentTime,
  outputType,
  onOutputTypeChange,
}: ClipPanelProps) {
  // Anchored: with no waveform, the slider is the only view of the selection, so a zoom
  // about the video's middle would leave a selection elsewhere out of sight.
  const zoom = useZoom({
    duration,
    selectionStart: editor.startTime,
    selectionEnd: editor.endTime,
    anchorOnSelection: true,
  })

  return (
    <div className="space-y-4 pt-4 border-t border-border">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h4 className="font-mono text-sm text-matrix">Clip Selection</h4>
          <p className="text-xs text-text-muted">The clip is cut from YouTube.</p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onExit}
          className="h-8 w-8 p-0 shrink-0"
          aria-label="Stop clipping"
        >
          <XMarkIcon className="h-4 w-4" />
        </Button>
      </div>

      <ZoomControls
        zoomLevel={zoom.zoomLevel}
        onZoomIn={zoom.zoomIn}
        onZoomOut={zoom.zoomOut}
        onZoomToSelection={zoom.zoomToSelection}
        onResetZoom={zoom.resetZoom}
        isZoomed={zoom.isZoomed}
        compact
      />

      <ClipTrimControls
        startTime={editor.startTime}
        endTime={editor.endTime}
        clipDuration={editor.clipDuration}
        duration={duration}
        currentTime={currentTime}
        isZoomed={zoom.isZoomed}
        viewStart={zoom.viewStart}
        viewEnd={zoom.viewEnd}
        onStartChange={editor.handleStartTimeChange}
        onEndChange={editor.handleEndTimeChange}
        onSetStartFromCurrent={editor.handleSetStartFromCurrent}
        onSetEndFromCurrent={editor.handleSetEndFromCurrent}
      />

      <div className="flex gap-2">
        <Button
          variant={editor.isLooping ? "matrix" : "outline"}
          size="sm"
          onClick={editor.toggleLoop}
          className="gap-2"
        >
          <ArrowPathIcon className={`h-4 w-4 ${editor.isLooping ? "animate-spin" : ""}`} />
          {editor.isLooping ? "Stop Loop" : "Preview Loop"}
        </Button>
        <Button
          variant="matrix"
          size="sm"
          onClick={editor.handleCreateClip}
          className="gap-2 flex-1"
        >
          <ScissorsIcon className="h-4 w-4" />
          Create Clip
        </Button>
      </div>

      {editor.showSaveDialog && (
        <ClipSaveDialog
          title={editor.title}
          setTitle={editor.setTitle}
          description={editor.description}
          setDescription={editor.setDescription}
          saving={editor.saving}
          onCancel={() => editor.setShowSaveDialog(false)}
          onSave={editor.handleSaveClip}
          outputType={outputType}
          onOutputTypeChange={onOutputTypeChange}
        />
      )}
    </div>
  )
}
