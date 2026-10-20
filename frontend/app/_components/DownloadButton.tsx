"use client"

import axios from "axios"
import { apiUrl, errorMessage } from "@/app/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  QUALITY_OPTIONS,
  AUDIO_QUALITY_OPTIONS,
  INITIAL_DOWNLOAD_OPTIONS,
  OptionToggle,
  TRANSCRIPT_ONLY_HINT,
  formMediaType,
} from "./downloadOptions"
import { isValidURL } from "../utils"
import toast from "react-hot-toast"
import { DownloadOptionsProps } from "../types/DownloadsOptions"
import { ArrowDownTrayIcon, XMarkIcon } from "@heroicons/react/24/outline"

export function DownloadButton({ options, setOptions }: DownloadOptionsProps) {
  const handleUrlChange = (event: { target: { value: any } }) => {
    setOptions({
      ...options,
      url: event.target.value,
    })
  }

  const handleClick = async () => {
    if (!isValidURL(options.url)) {
      toast.error("Not a valid URL")
      return
    }

    try {
      const response = await axios.post(
        apiUrl('/ytdl/'),
        {
          ...options,
          media_type: formMediaType(options),
        }
      )
      if (response.status === 201) {
        toast.success(options.transcript_only ? "Started transcript-only job" : "Started Download job")
      }
    } catch (error) {
      console.error("Error POST to /ytdl/", error)
      toast.error(errorMessage(error, "Failed to start download job"))
    }
  }

  return (
    <div className="space-y-3 w-full">
      <div className="flex flex-col sm:flex-row gap-3">
        <Input
          placeholder="Paste video URL..."
          label="Download URL"
          value={options.url}
          onChange={handleUrlChange}
          wrapperClassName="flex-1"
        />
        <div className="flex gap-3 sm:self-end">
          <Button
            variant="matrix"
            onClick={handleClick}
            className="h-10 gap-2 flex-1 sm:flex-none"
            title="Start Download"
          >
            <ArrowDownTrayIcon className="h-4 w-4" />
            <span className="hidden sm:inline">Start Download</span>
          </Button>
          <Button
            variant="outline"
            onClick={() => setOptions(INITIAL_DOWNLOAD_OPTIONS)}
            className="h-10 gap-2 flex-1 sm:flex-none"
            title="Clear download options"
          >
            <XMarkIcon className="h-4 w-4" />
            Clear
          </Button>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <OptionToggle
          label="Transcript Only"
          title={TRANSCRIPT_ONLY_HINT}
          checked={options.transcript_only}
          onChange={() => setOptions({ ...options, transcript_only: !options.transcript_only })}
        />
        {!options.transcript_only && (
          <OptionToggle
            label="Audio Only"
            checked={options.audio_only}
            onChange={() => setOptions({ ...options, audio_only: !options.audio_only })}
          />
        )}
        <OptionToggle
          label="Playlist"
          checked={options.download_playlist}
          onChange={() => setOptions({ ...options, download_playlist: !options.download_playlist })}
        />
        <OptionToggle
          label="Overwrite"
          checked={options.overwrite}
          onChange={() => setOptions({ ...options, overwrite: !options.overwrite })}
        />
        {!options.transcript_only && (
          <OptionToggle
            label="Transcript"
            checked={options.generate_transcript}
            onChange={() => setOptions({ ...options, generate_transcript: !options.generate_transcript })}
          />
        )}
        {!options.transcript_only && !options.audio_only && (
          <select
            value={options.download_quality}
            onChange={(e) => setOptions({ ...options, download_quality: e.target.value })}
            className="px-3 py-1.5 rounded-md text-sm font-mono bg-bg-surface border border-border text-text-secondary cursor-pointer"
          >
            {QUALITY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        )}
        {!options.transcript_only && options.audio_only && (
          <select
            value={options.audio_quality}
            onChange={(e) => setOptions({ ...options, audio_quality: e.target.value })}
            className="px-3 py-1.5 rounded-md text-sm font-mono bg-bg-surface border border-border text-text-secondary cursor-pointer"
            title="Audio quality (max bitrate)"
          >
            {AUDIO_QUALITY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        )}
      </div>
    </div>
  )
}
