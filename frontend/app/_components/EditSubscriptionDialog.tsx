"use client"

import { Dispatch, SetStateAction, useEffect, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import axios from "axios"
import { SubscriptionType } from "@/app/types/SubscriptionsOptions"
import toast from "react-hot-toast"
import { apiUrl } from "@/app/lib/api"
import { TrashIcon } from "@heroicons/react/24/outline"
import { ConfirmDialog, ConfirmDetailGrid } from "./ConfirmDialog"
import {
  QUALITY_OPTIONS,
  AUDIO_QUALITY_OPTIONS,
  DEFAULT_SUBSCRIPTION,
} from "./downloadOptions"

type EditSubscriptionDialogProps = {
  id: number
  open: boolean
  onOpenChange: (open: boolean) => void
  fetchSubscriptions: (
    search: string | null,
    page_number: number
  ) => Promise<any>
  subscriptions: SubscriptionType[]
  setSubscriptions: Dispatch<SetStateAction<SubscriptionType[]>>
}

export function EditSubscriptionDialog({
  id,
  open,
  onOpenChange,
  fetchSubscriptions,
  subscriptions,
  setSubscriptions,
}: EditSubscriptionDialogProps) {
  const [openConfirmation, setOpenConfirmation] = useState(false)
  const [focusSubscription, setFocusSubscription] =
    useState<SubscriptionType>(DEFAULT_SUBSCRIPTION)
  const [isLoading, setIsLoading] = useState(true)
  const transcriptOnly = focusSubscription.media_type === "TRANSCRIPT"

  const handleOpenConfirmation = () => {
    setOpenConfirmation(!openConfirmation)
  }

  useEffect(() => {
    const sub: SubscriptionType | undefined = subscriptions.find(
      (sub) => sub.id === id
    )
    if (open) {
      if (sub) {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- edit-form buffer seeded from the list, then written by the form's own handlers; not a derivation of subscriptions.find()
        setFocusSubscription(sub)
        setIsLoading(false)
      } else {
        toast.error("Subscription not found")
      }
    } else {
      if (sub) {
        setFocusSubscription(sub)
      }
    }
  }, [open, id, subscriptions])

  const handleSubscriptionSave = () => {
    const putSubscription = async (
      id: number,
      subscription: SubscriptionType | undefined
    ) => {
      await axios
        .put(
          apiUrl(`/subscriptions/${id}`),
          subscription
        )
        .then((response) => {
          if (response.status === 201) {
            toast.success("Saved Subscription")
          }
        })
        .catch((error) => {
          console.error("Error posting to /subscriptions", error)
          toast.error("An error occurred")
        })
    }

    putSubscription(id, focusSubscription)
    const updatedSubscriptions = subscriptions.map((sub) =>
      sub.id === focusSubscription.id ? focusSubscription : sub
    )
    setSubscriptions(updatedSubscriptions)
    onOpenChange(false)
  }

  const deleteConfirmed = () => {
    const deleteSubscription = async (id: number) => {
      await axios
        .delete(apiUrl(`/subscriptions/${id}`))
        .then((response) => {
          if (response.status === 204) {
            toast.success("Deleted Subscription")
            return fetchSubscriptions(null, 1).then((response) => {
              setSubscriptions(response.tableRows)
            })
          }
        })
        .catch(() => {
          toast.error("An error occurred")
        })
    }
    deleteSubscription(id)
    handleOpenConfirmation()
    onOpenChange(false)
  }

  const handleInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = event.target
    setFocusSubscription({ ...focusSubscription, [name]: value })
  }

  const handleCheckboxChange = (name: string, checked: boolean) => {
    setFocusSubscription({ ...focusSubscription, [name]: checked })
  }

  return (
    <>
      {!isLoading && (
        <Dialog open={open} onOpenChange={onOpenChange}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Edit Subscription</DialogTitle>
              <p className="text-sm text-text-secondary font-mono">
                {focusSubscription?.channel}
              </p>
            </DialogHeader>

            <div className="space-y-4 py-4">
              <Input
                name="string_match"
                label="Text Filter"
                placeholder="Filter videos by title..."
                value={focusSubscription.string_match || ""}
                onChange={handleInputChange}
              />

              <div className="flex gap-3">
                <div className="flex-1 space-y-1">
                  <label
                    htmlFor="min_duration_edit"
                    className="text-sm text-text-primary"
                  >
                    Min Duration (minutes)
                  </label>
                  <Input
                    id="min_duration_edit"
                    type="number"
                    placeholder="No minimum"
                    value={focusSubscription.min_duration_seconds ? (focusSubscription.min_duration_seconds / 60).toString() : ""}
                    onChange={(e) => {
                      const val = parseFloat(e.target.value)
                      setFocusSubscription({
                        ...focusSubscription,
                        min_duration_seconds: isNaN(val) ? null : Math.round(val * 60),
                      })
                    }}
                    min="0"
                    step="0.5"
                  />
                </div>
                <div className="flex-1 space-y-1">
                  <label
                    htmlFor="max_duration_edit"
                    className="text-sm text-text-primary"
                  >
                    Max Duration (minutes)
                  </label>
                  <Input
                    id="max_duration_edit"
                    type="number"
                    placeholder="No maximum"
                    value={focusSubscription.max_duration_seconds ? (focusSubscription.max_duration_seconds / 60).toString() : ""}
                    onChange={(e) => {
                      const val = parseFloat(e.target.value)
                      setFocusSubscription({
                        ...focusSubscription,
                        max_duration_seconds: isNaN(val) ? null : Math.round(val * 60),
                      })
                    }}
                    min="0"
                    step="0.5"
                  />
                </div>
              </div>

              <div className="space-y-3">
                {transcriptOnly && (
                  <p className="text-sm text-text-muted">
                    Transcript only: indexes each video&apos;s transcript without keeping the
                    media. The mode is fixed once the subscription exists.
                  </p>
                )}
                {!transcriptOnly && (
                  <div className="flex items-center gap-3">
                    <Checkbox
                      id="audio_only_edit"
                      checked={focusSubscription.audio_only}
                      onCheckedChange={(checked) =>
                        handleCheckboxChange("audio_only", checked === true)
                      }
                    />
                    <label
                      htmlFor="audio_only_edit"
                      className="text-sm text-text-primary cursor-pointer"
                    >
                      Audio Only
                    </label>
                  </div>
                )}

                <div className="flex items-center gap-3">
                  <Checkbox
                    id="overwrite_edit"
                    checked={focusSubscription.overwrite}
                    onCheckedChange={(checked) =>
                      handleCheckboxChange("overwrite", checked === true)
                    }
                  />
                  <label
                    htmlFor="overwrite_edit"
                    className="text-sm text-text-primary cursor-pointer"
                  >
                    Overwrite Existing
                  </label>
                </div>

                {!transcriptOnly && (
                  <div className="flex items-center gap-3">
                    <Checkbox
                      id="generate_transcript_edit"
                      checked={focusSubscription.generate_transcript}
                      onCheckedChange={(checked) =>
                        handleCheckboxChange("generate_transcript", checked === true)
                      }
                    />
                    <label
                      htmlFor="generate_transcript_edit"
                      className="text-sm text-text-primary cursor-pointer"
                    >
                      Generate Transcript
                    </label>
                  </div>
                )}
              </div>

              {!transcriptOnly && !focusSubscription.audio_only && (
                <div className="space-y-1">
                  <label
                    htmlFor="download_quality_edit"
                    className="text-sm text-text-primary"
                  >
                    Download Quality
                  </label>
                  <select
                    id="download_quality_edit"
                    value={focusSubscription.download_quality}
                    onChange={(e) =>
                      setFocusSubscription({
                        ...focusSubscription,
                        download_quality: e.target.value,
                      })
                    }
                    className="w-full px-3 py-2 rounded-md text-sm font-mono bg-bg-surface border border-border text-text-secondary cursor-pointer"
                  >
                    {QUALITY_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              {!transcriptOnly && focusSubscription.audio_only && (
                <div className="space-y-1">
                  <label
                    htmlFor="audio_quality_edit"
                    className="text-sm text-text-primary"
                  >
                    Audio Quality
                  </label>
                  <select
                    id="audio_quality_edit"
                    value={focusSubscription.audio_quality}
                    onChange={(e) =>
                      setFocusSubscription({
                        ...focusSubscription,
                        audio_quality: e.target.value,
                      })
                    }
                    className="w-full px-3 py-2 rounded-md text-sm font-mono bg-bg-surface border border-border text-text-secondary cursor-pointer"
                  >
                    {AUDIO_QUALITY_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>

            <DialogFooter className="gap-2 sm:gap-0">
              <Button variant="secondary" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button variant="destructive" onClick={handleOpenConfirmation}>
                Delete
              </Button>
              <Button variant="matrix" onClick={handleSubscriptionSave}>
                Save
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      <ConfirmDialog
        open={openConfirmation}
        onOpenChange={setOpenConfirmation}
        icon={<TrashIcon className="h-5 w-5 text-status-error" />}
        title="Confirm Delete"
        description="Are you sure you want to delete this item? This action cannot be undone."
        descriptionClassName="text-status-error/80"
        confirmLabel="Delete"
        onConfirm={deleteConfirmed}
      >
        <div className="py-4">
          <ConfirmDetailGrid
            rows={[
              {
                label: "URL:",
                value: focusSubscription.url,
                valueClassName: "text-text-primary truncate",
              },
              { label: "Channel:", value: focusSubscription.channel },
              { label: "Type:", value: focusSubscription.media_type },
              { label: "Filter:", value: focusSubscription.string_match || "None" },
            ]}
          />
        </div>
      </ConfirmDialog>
    </>
  )
}
