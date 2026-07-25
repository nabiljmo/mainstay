// The API base URL. Baked in at build time from VITE_API_URL (set on the host);
// falls back to the local dev server so `npm run dev` works with no config.
export const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

// Turn an API error `detail` into a readable string. FastAPI validation errors
// come back as a list of {loc, msg} objects, which naively rendered read as
// "[object Object]" — flatten those to their messages.
export const errText = (detail) =>
  Array.isArray(detail)
    ? detail.map((e) => (e && e.msg ? `${(e.loc || []).slice(-1)}: ${e.msg}` : JSON.stringify(e))).join('; ')
    : typeof detail === 'string'
      ? detail
      : JSON.stringify(detail || 'request failed')
