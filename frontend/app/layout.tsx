import "./globals.css"
import type { Metadata, Viewport } from "next"
import { NavigationBar } from "@/app/_components/NavigationBar"
import { Toaster } from "react-hot-toast"
import { ViewProvider } from "./context/ViewContext"
import { MediaPlayerProvider } from "@/app/context/MediaPlayerContext"
import { AuthProvider } from "@/app/context/AuthContext"
import { OfflineProvider } from "@/app/context/OfflineContext"
import { AdminProvider } from "@/app/context/AdminContext"
import { AuthGuard } from "@/app/_components/AuthGuard"
import { ThumbnailFilters } from "@/app/_components/ThumbnailFilters"
import { ServiceWorkerRegistrar } from "@/app/_components/ServiceWorkerRegistrar"

export const metadata: Metadata = {
  title: "YT-Hoarder",
  description: "Helper for managing YouTube downloads",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "Hoarder", statusBarStyle: "black-translucent" },
  // Any explicit `icons` object makes Next drop the app/icon.svg file convention,
  // so the favicon has to be listed here alongside the apple icon.
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png",
  },
}

// themeColor is a single value while the app ships 92 themes, so it names the
// shared void background rather than any one theme's accent. viewportFit=cover is
// what lets the standalone display mode reach under the iOS home indicator — and,
// with statusBarStyle black-translucent, under the status bar. Nothing reserves
// that space, so every viewport-pinned surface pads itself with
// env(safe-area-inset-*): NavigationBar (top), the audio bar in page.tsx and the
// Toaster below (bottom).
export const viewport: Viewport = {
  themeColor: "#0a0a0a",
  viewportFit: "cover",
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" data-theme="matrix">
      <body className="font-sans">
        <Toaster
          position="bottom-left"
          // 16px is react-hot-toast's default inset; containerStyle is spread over it.
          containerStyle={{ zIndex: 99999, bottom: "calc(16px + env(safe-area-inset-bottom))" }}
          toastOptions={{
            style: {
              background: "var(--card-bg)",
              color: "var(--text-primary)",
              border: "1px solid var(--matrix-green)",
              borderRadius: "var(--radius)",
              boxShadow: "var(--shadow-glow)",
              fontFamily: "var(--font-sans)",
            },
            success: {
              style: {
                borderLeft: "4px solid var(--status-success)",
              },
              iconTheme: {
                primary: "var(--status-success)",
                secondary: "var(--bg-void)",
              },
            },
            error: {
              style: {
                borderLeft: "4px solid var(--status-error)",
              },
              iconTheme: {
                primary: "var(--status-error)",
                secondary: "var(--bg-void)",
              },
            },
          }}
        />
        <ThumbnailFilters />
        <ServiceWorkerRegistrar />
        {/* Outside AuthProvider: the auth bootstrap consults the mode to decide
            whether to wait a minute for a server that isn't coming. */}
        <OfflineProvider>
          <AuthProvider>
            <AuthGuard>
              <AdminProvider>
              <MediaPlayerProvider>
                <ViewProvider>
                  <div className="min-h-screen bg-bg-void bg-grid">
                    <NavigationBar />
                    {children}
                  </div>
                </ViewProvider>
              </MediaPlayerProvider>
              </AdminProvider>
            </AuthGuard>
          </AuthProvider>
        </OfflineProvider>
      </body>
    </html>
  )
}
