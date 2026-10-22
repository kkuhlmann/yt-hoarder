"use client"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ArrowPathIcon, CheckIcon, FilmIcon, MusicalNoteIcon } from "@heroicons/react/24/solid"
import type { ClipOutputType } from "@/app/_hooks/useClipEditor"

const OUTPUT_TYPES: { value: ClipOutputType; label: string; icon: typeof FilmIcon }[] = [
  { value: "VIDEO", label: "Video (MP4)", icon: FilmIcon },
  { value: "AUDIO", label: "Audio (MP3)", icon: MusicalNoteIcon },
]

export function ClipSaveDialog({
  title,
  setTitle,
  description,
  setDescription,
  saving,
  onCancel,
  onSave,
  outputType,
  onOutputTypeChange,
}: {
  title: string
  setTitle: (v: string) => void
  description: string
  setDescription: (v: string) => void
  saving: boolean
  onCancel: () => void
  onSave: () => void
  /** For a source that can be clipped either way; omit both to show no choice. */
  outputType?: ClipOutputType
  onOutputTypeChange?: (v: ClipOutputType) => void
}) {
  return (
    <div className="space-y-3 p-4 bg-bg-surface rounded-lg border border-border">
      <h5 className="font-mono text-sm text-text-primary">Save Clip</h5>
      {outputType && onOutputTypeChange && (
        <div className="flex gap-2" role="radiogroup" aria-label="Clip type">
          {OUTPUT_TYPES.map(({ value, label, icon: Icon }) => (
            <Button
              key={value}
              type="button"
              role="radio"
              aria-checked={outputType === value}
              variant={outputType === value ? "matrix" : "outline"}
              size="sm"
              onClick={() => onOutputTypeChange(value)}
              disabled={saving}
              className="flex-1 gap-2"
            >
              <Icon className="h-4 w-4" />
              {label}
            </Button>
          ))}
        </div>
      )}
      <div className="space-y-2">
        <Input
          placeholder="Clip title (required)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="text-sm"
        />
        <Input
          placeholder="Description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="text-sm"
        />
      </div>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" onClick={onCancel} className="flex-1" disabled={saving}>
          Cancel
        </Button>
        <Button
          variant="matrix"
          size="sm"
          onClick={onSave}
          className="flex-1 gap-2"
          disabled={saving || !title.trim()}
        >
          {saving ? (
            <>
              <ArrowPathIcon className="h-4 w-4 animate-spin" />
              Saving...
            </>
          ) : (
            <>
              <CheckIcon className="h-4 w-4" />
              Save Clip
            </>
          )}
        </Button>
      </div>
    </div>
  )
}
