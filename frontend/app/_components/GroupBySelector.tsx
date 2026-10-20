"use client"

import { useState } from "react"
import { FolderIcon, ChevronDownIcon, CheckIcon } from "@heroicons/react/20/solid"
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover"
import { GROUP_DIM_LABELS } from "@/app/_hooks/useDownloadGrouping"
import type { GroupDim } from "@/app/types/DownloadsOptions"

const DIMS: GroupDim[] = ["channel", "tag", "downloaded", "released"]

export function GroupBySelector({
  value,
  onChange,
  labels = GROUP_DIM_LABELS,
}: {
  value: GroupDim | null
  onChange: (dim: GroupDim | null) => void
  /** Per-scope wording; see groupDimLabels. */
  labels?: Record<GroupDim, string>
}) {
  const [open, setOpen] = useState(false)

  const select = (dim: GroupDim | null) => {
    onChange(dim)
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          className={`inline-flex items-center gap-1.5 px-2 sm:px-2.5 py-1 rounded-md text-xs font-mono transition-colors ${
            value
              ? "bg-matrix/20 text-matrix border border-matrix/30"
              : "bg-bg-surface text-text-muted hover:text-text-secondary border border-border"
          }`}
          title="Group media into folders"
        >
          <FolderIcon className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">
            {value ? labels[value] : "Group"}
          </span>
          <ChevronDownIcon className="h-3 w-3 hidden sm:block" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-48 p-1">
        <OptionButton
          label="None"
          active={value === null}
          onClick={() => select(null)}
        />
        <div className="my-1 border-t border-border" />
        {DIMS.map((dim) => (
          <OptionButton
            key={dim}
            label={labels[dim]}
            active={value === dim}
            onClick={() => select(dim)}
          />
        ))}
      </PopoverContent>
    </Popover>
  )
}

function OptionButton({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center justify-between gap-2 px-2 py-1.5 rounded text-xs font-mono text-left transition-colors ${
        active ? "bg-matrix/10 text-matrix" : "text-text-secondary hover:bg-bg-surface"
      }`}
    >
      {label}
      {active && <CheckIcon className="h-3.5 w-3.5 shrink-0" />}
    </button>
  )
}
