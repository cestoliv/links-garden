# Links Garden dashboard

React + Vite + TypeScript frontend for the Links Garden API.

## Run

```bash
npm install
npm run dev
```

`npm run dev` proxies `/search`, `/sets`, `/documents`, `/review`, `/ingest`, and `/health`
to `http://127.0.0.1:8791` (see `vite.config.ts`), so the API needs no CORS headers. Enter
the API token in the sign-in screen; it stays in memory only, never in `localStorage`.

To point at a different API address, or a build not served through the dev proxy, set
`VITE_API_BASE_URL` (see `.env.example`).

## Checks

```bash
npx tsc --noEmit   # typecheck
npx eslint .       # lint
npm test           # vitest
```
