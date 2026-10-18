"use client"

import { useState } from "react"
import axios from "axios"
import { apiUrl, errorMessage } from "@/app/lib/api"
import { useAuth } from "@/app/context/AuthContext"
import { MIN_PASSWORD_LENGTH } from "./changePassword"
import { authInputClass, authSubmitClass } from "./styles"

export function AdminRecoveryPanel({ onBack }: { onBack: () => void }) {
  const { refreshAuth } = useAuth()
  const [step, setStep] = useState<"request" | "redeem">("request")
  const [username, setUsername] = useState("")
  const [code, setCode] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [filePath, setFilePath] = useState("")
  const [expiresInMinutes, setExpiresInMinutes] = useState(15)
  const [error, setError] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleRequest = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")
    setIsSubmitting(true)
    try {
      const resp = await axios.post(apiUrl("/auth/admin-recovery/request"), { username })
      setFilePath(resp.data.file_path)
      setExpiresInMinutes(resp.data.expires_in_minutes)
      setStep("redeem")
    } catch (err) {
      setError(errorMessage(err, "Could not reach the server. Try again."))
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleRedeem = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")

    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters`)
      return
    }
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match")
      return
    }

    setIsSubmitting(true)
    try {
      await axios.post(apiUrl("/auth/admin-recovery/complete"), {
        username,
        code: code.trim(),
        new_password: newPassword,
      })
      await refreshAuth()
    } catch (err) {
      setError(errorMessage(err, "Failed to reset password"))
    } finally {
      setIsSubmitting(false)
    }
  }

  if (step === "request") {
    return (
      <>
        <p className="text-text-muted font-mono text-xs mb-4">
          Recovering an admin account requires access to the machine yt-hoarder runs on. A
          one-time code will be written to a file there.
        </p>

        <form onSubmit={handleRequest} className="space-y-4">
          <div>
            <label className="block text-sm font-mono text-text-secondary mb-1">
              Admin username
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className={authInputClass}
              autoFocus
              required
            />
          </div>

          {error && <p className="text-sm font-mono text-status-error">{error}</p>}

          <button
            type="submit"
            disabled={isSubmitting}
            className={authSubmitClass}
          >
            {isSubmitting ? "Writing code..." : "Write Recovery Code"}
          </button>
        </form>

        <div className="mt-6 text-center">
          <button
            onClick={onBack}
            className="text-sm font-mono text-text-muted hover:text-matrix transition-colors"
          >
            Back to sign in
          </button>
        </div>
      </>
    )
  }

  return (
    <>
      <div className="border border-border rounded p-3 mb-4 bg-bg-void/50">
        <p className="text-text-secondary font-mono text-xs mb-2">
          On the server, read the code from:
        </p>
        <code className="block text-matrix font-mono text-xs break-all">{filePath}</code>
        <p className="text-text-muted font-mono text-[11px] mt-2">
          That&apos;s <span className="text-text-secondary">data/admin-recovery.txt</span> inside
          your install directory on the host. Expires in {expiresInMinutes} minutes.
        </p>
      </div>

      <form onSubmit={handleRedeem} className="space-y-4">
        <div>
          <label className="block text-sm font-mono text-text-secondary mb-1">Recovery code</label>
          <input
            type="text"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className={`${authInputClass} tracking-widest uppercase`}
            placeholder="XXXX-XXXX-XXXX"
            autoFocus
            required
          />
        </div>

        <div>
          <label className="block text-sm font-mono text-text-secondary mb-1">New password</label>
          <input
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className={authInputClass}
            autoComplete="new-password"
            required
          />
        </div>

        <div>
          <label className="block text-sm font-mono text-text-secondary mb-1">
            Confirm new password
          </label>
          <input
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className={authInputClass}
            autoComplete="new-password"
            required
          />
        </div>

        {error && <p className="text-sm font-mono text-status-error">{error}</p>}

        <button
          type="submit"
          disabled={isSubmitting}
          className={authSubmitClass}
        >
          {isSubmitting ? "Resetting..." : "Reset Password & Sign In"}
        </button>
      </form>

      <div className="mt-6 text-center">
        <button
          onClick={onBack}
          className="text-sm font-mono text-text-muted hover:text-matrix transition-colors"
        >
          Back to sign in
        </button>
      </div>
    </>
  )
}
