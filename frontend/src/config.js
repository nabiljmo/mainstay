// The API base URL. Baked in at build time from VITE_API_URL (set on the host);
// falls back to the local dev server so `npm run dev` works with no config.
export const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'
