/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API (src/links_garden/api.py) sends no CORS headers, so a direct browser fetch from the
// dev server's origin is blocked. Proxying these routes server-to-server sidesteps CORS entirely
// instead of asking the Python side to add headers.
//
// 8000 is `garden serve`'s own default port (cli.py). If you start the API on another port, set
// GARDEN_API_URL to match, or the dashboard proxies to a port with nothing behind it and every
// sign-in fails with a token error that looks like a bad token.
const API_PROXY_TARGET = process.env.GARDEN_API_URL ?? 'http://127.0.0.1:8000'
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
