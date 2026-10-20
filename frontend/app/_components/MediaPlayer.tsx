"use client"

import { useEffect, useRef, useState, useCallback, type RefObject } from "react"
import { apiUrl } from "@/app/lib/api"
import { mediaApi } from "@/app/lib/mediaApi"
import { formatTime } from "@/app/utils"
import { type MediaSessionQueue } from "@/app/_hooks/useMediaSession"
import { useMediaElement } from "@/app/_hooks/useMediaElement"
import { Button } from "@/components/ui/button"
import { Slider } from "@/components/ui/slider"
import {
  PlayIcon,
  PauseIcon,
  SpeakerWaveIcon,
  SpeakerXMarkIcon,
  BackwardIcon,
  ForwardIcon,
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
  ChartBarIcon,
  PresentationChartLineIcon,
  SignalSlashIcon,
} from "@heroicons/react/24/solid"
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"
import { PreviewTooltip } from "./PreviewTooltip"
import { useAudioAnalyser, type AudioAnalyserHandle } from "@/app/_hooks/useAudioAnalyser"

// WebKit (iPhone Safari) fullscreen + Picture-in-Picture APIs — not in lib.dom types
interface WebkitPresentationVideo extends HTMLVideoElement {
  webkitSupportsPresentationMode?: (mode: "picture-in-picture" | "inline" | "fullscreen") => boolean
  webkitSetPresentationMode?: (mode: "picture-in-picture" | "inline" | "fullscreen") => void
  webkitPresentationMode?: "picture-in-picture" | "inline" | "fullscreen"
  webkitEnterFullscreen?: () => void // iPhone native fullscreen video player
}

// Picture-in-Picture glyph (heroicons has none): outer screen + inset window
function PipIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" className={className} aria-hidden="true">
      <rect x="3" y="4" width="18" height="14" rx="2" strokeWidth={1.8} />
      <rect x="12" y="10.5" width="7" height="5.5" rx="1" fill="currentColor" stroke="none" />
    </svg>
  )
}

const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3]

export function VideoPlayer({
  id,
  startTime = 0,
  duration: initialDuration,
  exactStart = false,
  onTimeUpdate,
  videoRefCallback,
  isClip = false,
  onEnded,
  queue,
}: {
  id: number
  startTime?: number
  duration?: number
  /** `startTime` is a timestamp the user picked, not a resume position. */
  exactStart?: boolean
  onTimeUpdate?: (time: number) => void
  videoRefCallback?: (ref: HTMLVideoElement | null) => void
  isClip?: boolean
  onEnded?: () => void
  queue?: MediaSessionQueue
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [pipActive, setPipActive] = useState(false)
  const [pipSupported, setPipSupported] = useState(false)
  const [showControls, setShowControls] = useState(true)
  const controlsTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const [isHoveringProgress, setIsHoveringProgress] = useState(false)
  // Progress-bar geometry for the preview tooltip, measured in the mouse
  // handler. The rect is only knowable from a real pointer event, so measuring
  // it there beats handing the tooltip a ref to read while it renders.
  const [hover, setHover] = useState({ x: 0, width: 0 })

  const {
    mediaSrc,
    isPlaying,
    currentTime,
    duration,
    volume,
    isMuted,
    playbackRate,
    togglePlay,
    handleSeek,
    handleVolumeChange,
    toggleMute,
    skip,
    handlePlaybackRateChange,
  } = useMediaElement(videoRef, {
    id,
    startTime,
    initialDuration,
    exactStart,
    isClip,
    onEnded,
    onTimeUpdate,
    queue,
  })

  useEffect(() => {
    if (videoRefCallback) {
      videoRefCallback(videoRef.current)
    }
    return () => {
      if (videoRefCallback) {
        videoRefCallback(null)
      }
    }
  }, [videoRefCallback])

  // Track fullscreen (desktop/iPad) and Picture-in-Picture (iPhone) presentation state
  useEffect(() => {
    const video = videoRef.current

    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement)
    }
    const handleEnterPip = () => setPipActive(true)
    const handleLeavePip = () => setPipActive(false)
    // iPhone Safari signals PiP via webkitpresentationmodechanged, not enter/leave events
    const handleWebkitPresentationChange = () => {
      const wv = video as WebkitPresentationVideo | null
      setPipActive(wv?.webkitPresentationMode === "picture-in-picture")
    }

    document.addEventListener("fullscreenchange", handleFullscreenChange)
    video?.addEventListener("enterpictureinpicture", handleEnterPip)
    video?.addEventListener("leavepictureinpicture", handleLeavePip)
    video?.addEventListener("webkitpresentationmodechanged", handleWebkitPresentationChange)

    return () => {
      document.removeEventListener("fullscreenchange", handleFullscreenChange)
      video?.removeEventListener("enterpictureinpicture", handleEnterPip)
      video?.removeEventListener("leavepictureinpicture", handleLeavePip)
      video?.removeEventListener("webkitpresentationmodechanged", handleWebkitPresentationChange)
    }
  }, [])

  // Detect Picture-in-Picture support so we can conditionally show the PiP button.
  // iPhone's webkitSupportsPresentationMode can become reliable only once metadata loads.
  useEffect(() => {
    const video = videoRef.current as WebkitPresentationVideo | null
    if (!video) return

    const check = () =>
      setPipSupported(
        video.webkitSupportsPresentationMode?.("picture-in-picture") === true ||
          document.pictureInPictureEnabled === true
      )

    check()
    video.addEventListener("loadedmetadata", check)
    return () => video.removeEventListener("loadedmetadata", check)
  }, [])

  const resetControlsTimeout = useCallback(() => {
    setShowControls(true)
    if (controlsTimeoutRef.current) {
      clearTimeout(controlsTimeoutRef.current)
    }
    if (isPlaying) {
      controlsTimeoutRef.current = setTimeout(() => {
        setShowControls(false)
      }, 3000)
    }
  }, [isPlaying])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- drives an external timer; the setShowControls(true) inside restarts the 3s auto-hide
    resetControlsTimeout()
    return () => {
      if (controlsTimeoutRef.current) {
        clearTimeout(controlsTimeoutRef.current)
      }
    }
  }, [isPlaying, resetControlsTimeout])

  const toggleFullscreen = useCallback(async () => {
    const video = videoRef.current
    const container = containerRef.current
    if (!video || !container) return

    // Desktop / iPad: element Fullscreen API on the container keeps our custom overlay.
    if (document.fullscreenEnabled && typeof container.requestFullscreen === "function") {
      if (!document.fullscreenElement) await container.requestFullscreen().catch(() => {})
      else await document.exitFullscreen().catch(() => {})
      return
    }

    // iPhone Safari: no element Fullscreen API — use the native fullscreen video
    // player, which continues playback when the screen locks.
    const wv = video as WebkitPresentationVideo
    if (typeof wv.webkitEnterFullscreen === "function") wv.webkitEnterFullscreen()
  }, [])

  const togglePip = useCallback(async () => {
    const video = videoRef.current
    if (!video) return

    // iPhone Safari uses the webkit presentation-mode API.
    const wv = video as WebkitPresentationVideo
    if (wv.webkitSupportsPresentationMode?.("picture-in-picture")) {
      wv.webkitSetPresentationMode?.(
        wv.webkitPresentationMode === "picture-in-picture" ? "inline" : "picture-in-picture"
      )
      return
    }

    // Standard PiP API (desktop Chrome/Edge, Android).
    if (document.pictureInPictureEnabled) {
      if (document.pictureInPictureElement) await document.exitPictureInPicture().catch(() => {})
      else await video.requestPictureInPicture().catch(() => {})
    }
  }, [])

  const handleVideoClick = useCallback(() => {
    togglePlay()
    resetControlsTimeout()
  }, [togglePlay, resetControlsTimeout])

  // Drives the maximize/minimize icon (desktop/iPad element fullscreen). PiP has
  // its own button; iPhone native fullscreen covers the whole UI so this is moot there.
  const isMaximized = isFullscreen

  return (
    <div
      ref={containerRef}
      className="relative w-full max-w-4xl mx-auto bg-black rounded-lg overflow-hidden border border-border shadow-lg"
      onMouseMove={resetControlsTimeout}
      onMouseLeave={() => isPlaying && setShowControls(false)}
    >
      <video
        ref={videoRef}
        src={mediaSrc}
        className="w-full aspect-video cursor-pointer"
        onClick={handleVideoClick}
        autoPlay
        playsInline
        preload="metadata"
        loop={isClip}
        poster={isClip ? undefined : apiUrl(mediaApi.thumbnail(id))}
      />

      <div
        className={cn(
          "absolute bottom-0 left-0 right-0 bg-linear-to-t from-black/90 via-black/50 to-transparent",
          "transition-opacity duration-300 p-4",
          showControls ? "opacity-100" : "opacity-0 pointer-events-none"
        )}
      >
        <div
          className="mb-3 relative"
          onMouseMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect()
            setHover({ x: e.clientX - rect.left, width: rect.width })
            setIsHoveringProgress(true)
          }}
          onMouseLeave={() => setIsHoveringProgress(false)}
        >
          {!isClip && (
            <PreviewTooltip
              mediaId={id}
              duration={duration}
              containerWidth={hover.width}
              relativeX={hover.x}
              visible={isHoveringProgress}
            />
          )}
          <Slider
            value={currentTime}
            onChange={handleSeek}
            min={0}
            max={duration || 100}
            step={0.1}
          />
        </div>

        {/* Controls row */}
        <div className="flex items-center gap-2 sm:gap-3">
          {/* Play/Pause */}
          <Button
            variant="ghost"
            size="icon"
            onClick={togglePlay}
            className="h-10 w-10 text-white hover:text-matrix hover:bg-white/10"
          >
            {isPlaying ? (
              <PauseIcon className="h-6 w-6" />
            ) : (
              <PlayIcon className="h-6 w-6 ml-0.5" />
            )}
          </Button>

          {/* Skip back */}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => skip(-10)}
            className="h-8 w-8 text-white/70 hover:text-matrix hover:bg-white/10"
            title="Back 10s"
          >
            <BackwardIcon className="h-4 w-4" />
          </Button>

          {/* Skip forward */}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => skip(10)}
            className="h-8 w-8 text-white/70 hover:text-matrix hover:bg-white/10"
            title="Forward 10s"
          >
            <ForwardIcon className="h-4 w-4" />
          </Button>

          {/* Time display */}
          <span className="font-mono text-xs text-white/80 min-w-[100px]">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>

          {/* Spacer */}
          <div className="flex-1" />

          {/* Volume */}
          <div className="hidden sm:flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleMute}
              className="h-8 w-8 text-white/70 hover:text-matrix hover:bg-white/10"
            >
              {isMuted ? (
                <SpeakerXMarkIcon className="h-4 w-4" />
              ) : (
                <SpeakerWaveIcon className="h-4 w-4" />
              )}
            </Button>
            <div className="w-32">
              <Slider
                value={isMuted ? 0 : volume}
                onChange={handleVolumeChange}
                min={0}
                max={1}
                step={0.01}
              />
            </div>
          </div>

          {/* Playback speed */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                className="h-8 px-2 text-xs font-mono text-white/70 hover:text-matrix hover:bg-white/10"
              >
                {playbackRate}x
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="center" side="top">
              <DropdownMenuRadioGroup value={String(playbackRate)} onValueChange={handlePlaybackRateChange}>
                {PLAYBACK_RATES.map((rate) => (
                  <DropdownMenuRadioItem key={rate} value={String(rate)}>
                    {rate}x
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>

          {/* Picture-in-Picture */}
          {pipSupported && (
            <Button
              variant="ghost"
              size="icon"
              onClick={togglePip}
              className="h-8 w-8 text-white/70 hover:text-matrix hover:bg-white/10"
              title={pipActive ? "Exit Picture-in-Picture" : "Picture-in-Picture"}
            >
              <PipIcon className="h-4 w-4" />
            </Button>
          )}

          {/* Fullscreen */}
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleFullscreen}
            className="h-8 w-8 text-white/70 hover:text-matrix hover:bg-white/10"
            title={isMaximized ? "Minimize" : "Maximize"}
          >
            {isMaximized ? (
              <ArrowsPointingInIcon className="h-4 w-4" />
            ) : (
              <ArrowsPointingOutIcon className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>

      {/* Center play button overlay (when paused) */}
      {!isPlaying && showControls && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div
            className="w-16 h-16 rounded-full bg-matrix/80 flex items-center justify-center shadow-glow hover:bg-matrix transition-colors cursor-pointer pointer-events-auto"
            onClick={handleVideoClick}
          >
            <PlayIcon className="h-8 w-8 text-black ml-1" />
          </div>
        </div>
      )}
    </div>
  )
}

export function AudioPlayer({
  id,
  startTime = 0,
  exactStart = false,
  duration: initialDuration,
  isClip = false,
  onEnded,
  visualizerStyle = null,
  onCycleVisualizer,
  analyserHandleRef,
  queue,
  onElement,
}: {
  id: number
  startTime?: number
  exactStart?: boolean
  duration?: number
  isClip?: boolean
  onEnded?: () => void
  visualizerStyle?: "bars" | "area" | null
  onCycleVisualizer?: () => void
  analyserHandleRef?: RefObject<AudioAnalyserHandle | null>
  queue?: MediaSessionQueue
  /** Receives the <audio> element, so a page can follow and seek this player. */
  onElement?: (element: HTMLAudioElement | null) => void
}) {
  const audioRef = useRef<HTMLAudioElement>(null)

  useEffect(() => {
    if (!onElement) return
    onElement(audioRef.current)
    return () => onElement(null)
  }, [onElement])

  const {
    mediaSrc,
    isPlaying,
    currentTime,
    duration,
    volume,
    isMuted,
    playbackRate,
    togglePlay,
    handleSeek,
    handleVolumeChange,
    toggleMute,
    skip,
    handlePlaybackRateChange,
  } = useMediaElement(audioRef, {
    id,
    startTime,
    exactStart,
    initialDuration,
    isClip,
    onEnded,
    queue,
  })

  // Route the audio element through Web Audio (lazily, only when enabled) so the
  // visualizer can read frequency data. See useAudioAnalyser for the iOS caveat.
  const analyser = useAudioAnalyser(audioRef, {
    enabled: visualizerStyle !== null,
    isPlaying,
  })

  // Publish the analyser handle to the footer so its backdrop layer can read it.
  useEffect(() => {
    if (!analyserHandleRef) return
    analyserHandleRef.current = analyser
    return () => {
      analyserHandleRef.current = null
    }
  }, [analyser, analyserHandleRef])

  const handleCycleVisualizer = useCallback(() => {
    // Start/resume the AudioContext inside this user gesture; creating it later
    // (in an effect) leaves it suspended, routing the audio into a silent graph.
    analyser.ensureStarted()
    onCycleVisualizer?.()
  }, [analyser, onCycleVisualizer])

  return (
    <div className="w-full">
      {/* Hidden audio element.
          crossOrigin="use-credentials" makes the element CORS-readable so the
          visualizer's Web Audio AnalyserNode gets real samples instead of
          zeroes when the backend is a different origin (e.g. dev :3000/:8000) —
          a tainted cross-origin element zeroes frequency data. "use-credentials"
          (not "anonymous") so the auth cookie is still sent to the cookie-
          protected /media route; the backend reflects the origin and sets
          Access-Control-Allow-Credentials, so playback is unaffected. */}
      <audio
        ref={audioRef}
        src={mediaSrc}
        crossOrigin="use-credentials"
        autoPlay
        preload="metadata"
        loop={isClip}
      />

      {/* Custom controls */}
      <div className="flex items-center gap-1.5 sm:gap-3">
        {/* Skip back */}
        <Button
          variant="ghost"
          size="icon"
          onClick={() => skip(-10)}
          className="hidden sm:flex h-8 w-8 text-text-secondary hover:text-matrix"
          title="Back 10s"
        >
          <BackwardIcon className="h-4 w-4" />
        </Button>

        {/* Play/Pause */}
        <Button
          variant="matrix"
          size="icon"
          onClick={togglePlay}
          className="h-10 w-10 rounded-full shrink-0"
        >
          {isPlaying ? (
            <PauseIcon className="h-5 w-5" />
          ) : (
            <PlayIcon className="h-5 w-5 ml-0.5" />
          )}
        </Button>

        {/* Skip forward */}
        <Button
          variant="ghost"
          size="icon"
          onClick={() => skip(10)}
          className="hidden sm:flex h-8 w-8 text-text-secondary hover:text-matrix"
          title="Forward 10s"
        >
          <ForwardIcon className="h-4 w-4" />
        </Button>

        {/* Time display */}
        <span className="hidden sm:inline font-mono text-xs text-text-muted min-w-[85px]">
          {formatTime(currentTime)} / {formatTime(duration)}
        </span>

        {/* Progress bar */}
        <div className="flex-1">
          <Slider
            value={currentTime}
            onChange={handleSeek}
            min={0}
            max={duration || 100}
            step={0.1}
          />
        </div>

        {/* Volume */}
        <div className="hidden sm:flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleMute}
            className="h-8 w-8 text-text-secondary hover:text-matrix"
          >
            {isMuted ? (
              <SpeakerXMarkIcon className="h-4 w-4" />
            ) : (
              <SpeakerWaveIcon className="h-4 w-4" />
            )}
          </Button>
          <div className="w-28">
            <Slider
              value={isMuted ? 0 : volume}
              onChange={handleVolumeChange}
              min={0}
              max={1}
              step={0.01}
            />
          </div>
        </div>

        {/* Playback speed */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className="h-8 px-2 text-xs font-mono text-text-secondary hover:text-matrix"
            >
              {playbackRate}x
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="center" side="top">
            <DropdownMenuRadioGroup value={String(playbackRate)} onValueChange={handlePlaybackRateChange}>
              {PLAYBACK_RATES.map((rate) => (
                <DropdownMenuRadioItem key={rate} value={String(rate)}>
                  {rate}x
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Visualizer cycle: off -> bars -> mountain (audio-only) */}
        {onCycleVisualizer && (
          <Button
            variant="ghost"
            size="icon"
            onClick={handleCycleVisualizer}
            className={cn(
              "hidden sm:flex h-8 w-8",
              visualizerStyle
                ? "text-matrix"
                : "text-text-secondary hover:text-matrix",
            )}
            title={
              visualizerStyle === "area"
                ? "Visualizer: mountain"
                : visualizerStyle === "bars"
                  ? "Visualizer: bars"
                  : "Visualizer: off"
            }
          >
            {visualizerStyle === "area" ? (
              <PresentationChartLineIcon className="h-4 w-4" />
            ) : visualizerStyle === "bars" ? (
              <ChartBarIcon className="h-4 w-4" />
            ) : (
              <SignalSlashIcon className="h-4 w-4" />
            )}
          </Button>
        )}
      </div>
    </div>
  )
}
