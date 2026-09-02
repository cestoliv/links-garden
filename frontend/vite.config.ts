/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API (src/links_garden/api.py) sends no CORS headers, so a direct browser fetch from the
// dev server's origin is blocked. Proxying these routes server-to-server sidesteps CORS entirely
// instead of asking the Python side to add headers. Matches src/config.ts's default API_BASE_URL.
const API_PROXY_TARGET = 'http://127.0.0.1:8791'
const API_ROUTES = ['/health', '/search', '/documents', '/sets', '/review', '/ingest']

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: Object.fromEntries(API_ROUTES.map((route) => [route, API_PROXY_TARGET])),
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
