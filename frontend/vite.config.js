import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icon-192.png", "icon-512.png", "manifest.json"],
      manifest: {
        name: "Shelf It",
        short_name: "ShelfIt",
        description: "Track groceries, cook from your fridge, cut waste.",
        start_url: "/",
        display: "standalone",
        background_color: "#f7f1e8",
        theme_color: "#f4a574",
        icons: [
          {
            src: "/icon-192.png",
            sizes: "192x192",
            type: "image/png"
          },
          {
            src: "/icon-512.png",
            sizes: "512x512",
            type: "image/png"
          }
        ]
      },
      workbox: {
        navigateFallback: "/index.html",
        // OAuth is a real navigation to /api/auth/google. Without this denylist
        // the service worker serves index.html instead, so Continue with Google
        // looks like a no-op on the login screen.
        navigateFallbackDenylist: [
          /^\/api\//,
          /^\/health/,
          /^\/docs/,
          /^\/openapi\.json/,
          /^\/redoc/
        ],
        runtimeCaching: [
          {
            urlPattern: ({ url, request }) =>
              request.method === "GET" &&
              url.pathname.startsWith("/api/") &&
              !url.pathname.startsWith("/api/auth/google"),
            handler: "NetworkFirst",
            options: {
              cacheName: "shelfit-api",
              networkTimeoutSeconds: 5,
              expiration: { maxEntries: 64, maxAgeSeconds: 60 * 60 }
            }
          }
        ]
      }
    })
  ],
  server: {
    port: 5173,
    host: true
  }
});
