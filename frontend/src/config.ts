// Relative by default: `npm run dev` proxies these routes to the real API (see vite.config.ts),
// avoiding the browser CORS block a direct cross-origin fetch would hit. Override to an absolute
// URL for a production build served without that dev proxy.
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? ''
