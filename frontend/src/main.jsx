import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

// The API lives on a different port, so cross-origin fetches don't send our
// session cookie unless credentials are included. Rather than thread this
// through every call site, add it once for requests aimed at the API origin.
import { API as API_ORIGIN } from './config.js'
const _fetch = window.fetch.bind(window)
window.fetch = (input, init = {}) => {
  const url = typeof input === 'string' ? input : input?.url
  if (url && url.startsWith(API_ORIGIN)) init = { credentials: 'include', ...init }
  return _fetch(input, init)
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
