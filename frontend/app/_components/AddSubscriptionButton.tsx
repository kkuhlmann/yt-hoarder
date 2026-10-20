"use client"

import axios from "axios"
import { apiUrl } from "@/app/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  QUALITY_OPTIONS,
  AUDIO_QUALITY_OPTIONS,
  OptionToggle,
  TRANSCRIPT_ONLY_HINT,
  formMediaType,
} from "./downloadOptions"
import { isValidSubscriptionUrl } from "../utils"
import toast from "react-hot-toast"
import { SubscriptionsProps, SubscriptionOptionsType } from "@/app/types/SubscriptionsOptions"
import { useEffect, useState } from "react"
import { startJobPolling } from "../jobStatusService"
import { PlusIcon } from "@heroicons/react/24/outline"
import { XMarkIcon } from "@heroicons/react/20/solid"
import DateSelection from "@/app/_components/DateSelection"

export function AddSubscriptionButton({
  options,
  setOptions,
  fetchSubscriptions,
  setSubscriptions,
  setPageCount,
  pageNumber,
}: SubscriptionsProps) {
  const [taskId, setTaskId] = useState(null)
  const [date, setDate] = useState<Date | null>(null)

  const handleUrlChange = (event: { target: { value: any } }) => {
    setOptions({
      ...options,
      url: event.target.value,
    })
  }

  const handleClearDateClick = () => {
    setDate(null)
    const { date_filter, ...newOptions } = options
    setOptions(newOptions as SubscriptionOptionsType)
  }

  const handleTextFilterChange = (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    setOptions({ ...options, string_match: event.target.value })
  }

  useEffect(() => {
    if (!taskId) return

    startJobPolling(
      "Subscription",
      taskId,
      () => {
        toast.success("Added Subscription", {
          id: "added-subscription-success",
        })
        if (pageNumber === 1) {
          fetchSubscriptions("", 1)
            .then(({ pageCount, tableRows }) => {
              setPageCount(pageCount)
              setSubscriptions(tableRows)
            })
            .catch(() => {
              console.error("Failed to fetch subscriptions")
            })
        }
      },
      () => toast.error("Failed to add Subscription"),
      (loadingToast) => {
        toast.loading("Getting Subscription URL information...", {
          id: loadingToast,
        })
      },
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId])

  const handleClick = async () => {
    if (!isValidSubscriptionUrl(options.url)) {
      toast.error("Invalid URL")
      return
    }

    axios
      .post(apiUrl("/subscriptions"), {
        ...options,
        media_type: formMediaType(options),
      })
      .then((response) => {
        if (response.status === 201) {
          if (response.data.task === "DUPLICATE_SUBSCRIPTION") {
            toast.error("You already have this subscription")
          } else {
            setTaskId(response.data.task)
          }
        }
      })
      .catch(() => {
        setTaskId(null)
        toast.error("An error occurred")
      })
  }

  return (
    <div className="space-y-3 w-full">
      <div className="flex flex-col sm:flex-row gap-3">
        <Input
          type="url"
          placeholder="Channel or Playlist URL"
          label="Subscription URL"
          onChange={handleUrlChange}
          wrapperClassName="flex-1"
        />
        <Button
          variant="matrix"
          onClick={handleClick}
          className="gap-2 sm:self-end"
          title="Add Subscription"
        >
          <PlusIcon className="h-4 w-4" />
          <span className="hidden sm:inline">Add Subscription</span>
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1">
          <DateSelection
            label="Date Filter"
            className="w-auto"
            setDate={setDate}
            date={date}
            options={options}
            setOptions={setOptions}
          />
          {date && (
            <Button
              variant="ghost"
              size="icon"
              onClick={handleClearDateClick}
              className="h-8 w-8"
            >
              <XMarkIcon className="h-4 w-4" />
            </Button>
          )}
        </div>

        <Input
          name="string_match"
          placeholder="Title filter..."
          value={options.string_match || ""}
          onChange={handleTextFilterChange}
          className="w-40 sm:w-48"
        />

        <Input
          type="number"
          placeholder="Min duration"
          value={
            options.min_duration_seconds
              ? (options.min_duration_seconds / 60).toString()
              : ""
          }
          onChange={(e) => {
            const val = parseFloat(e.target.value)
            setOptions({
              ...options,
              min_duration_seconds: isNaN(val) ? null : Math.round(val * 60),
            })
          }}
          className="w-40"
          min="0"
          step="0.5"
        />
        <Input
          type="number"
          placeholder="Max duration"
          value={
            options.max_duration_seconds
              ? (options.max_duration_seconds / 60).toString()
              : ""
          }
          onChange={(e) => {
            const val = parseFloat(e.target.value)
            setOptions({
              ...options,
              max_duration_seconds: isNaN(val) ? null : Math.round(val * 60),
            })
          }}
          className="w-40"
          min="0"
          step="0.5"
        />

        <OptionToggle
          label="Transcript Only"
          title={TRANSCRIPT_ONLY_HINT}
          checked={options.transcript_only}
          onChange={() =>
            setOptions({ ...options, transcript_only: !options.transcript_only })
          }
        />
        {!options.transcript_only && (
          <OptionToggle
            label="Audio Only"
            checked={options.audio_only}
            onChange={() =>
              setOptions({ ...options, audio_only: !options.audio_only })
            }
          />
        )}
        <OptionToggle
          label="Overwrite"
          checked={options.overwrite}
          onChange={() =>
            setOptions({ ...options, overwrite: !options.overwrite })
          }
        />
        {!options.transcript_only && (
          <OptionToggle
            label="Transcript"
            checked={options.generate_transcript}
            onChange={() =>
              setOptions({
                ...options,
                generate_transcript: !options.generate_transcript,
              })
            }
          />
        )}
        {!options.transcript_only && !options.audio_only && (
          <select
            value={options.download_quality}
            onChange={(e) =>
              setOptions({ ...options, download_quality: e.target.value })
            }
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
            onChange={(e) =>
              setOptions({ ...options, audio_quality: e.target.value })
            }
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
