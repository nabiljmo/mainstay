import { useEffect, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import WeatherPanel from './WeatherPanel.jsx'
import ZoningPanel from './ZoningPanel.jsx'
import CropLibrary from './CropLibrary.jsx'
import ProductDesign from './ProductDesign.jsx'
import UsersPanel from './UsersPanel.jsx'
import OperationsPanel from './OperationsPanel.jsx'
import Login from './Login.jsx'

const API = 'http://localhost:8000'

// Which tabs each role sees. admin is a superuser and sees everything.
const TABS = [
  { key: 'zoning', label: 'Zoning', roles: ['actuary'] },
  { key: 'crops', label: 'Crop library', roles: ['agronomist'] },
  { key: 'products', label: 'Products', roles: ['actuary'] },
  { key: 'ops', label: 'Operations', roles: ['operations'] },
  { key: 'users', label: 'Users', roles: ['admin'] },
]

const PALETTE = [
  '#1b9e77', '#d95f02', '#7570b3', '#e7298a', '#66a61e',
  '#e6ab02', '#a6761d', '#666666', '#1f78b4', '#b2df8a',
  '#fb9a99', '#fdbf6f', '#cab2d6', '#b15928', '#a6cee3',
  '#33a02c', '#ff7f00', '#6a3d9a', '#ffff99', '#8dd3c7',
]

function StatusDot({ label, state }) {
  const cls = state === 'ok' ? 'dot ok' : state ? 'dot bad' : 'dot'
  return (
    <span>
      <i className={cls} /> {label}
    </span>
  )
}

export default function App() {
  const mapContainer = useRef(null)
  const mapRef = useRef(null)
  const [mapReady, setMapReady] = useState(false)
  const [health, setHealth] = useState({})
  const [country, setCountry] = useState('KEN')
  const [zones, setZones] = useState(null)
  const [user, setUser] = useState(undefined) // undefined = still checking
  const [view, setView] = useState(null)

  const tabs = user ? TABS.filter((t) => user.role === 'admin' || t.roles.includes(user.role)) : []
  const canZone = tabs.some((t) => t.key === 'zoning')

  // Who am I? (session cookie is sent automatically by the fetch shim.)
  useEffect(() => {
    fetch(`${API}/auth/me`)
      .then((r) => (r.ok ? r.json() : null))
      .then((u) => {
        setUser(u)
        if (u) {
          const first = TABS.find((t) => u.role === 'admin' || t.roles.includes(u.role))
          setView(first ? first.key : 'none')
        }
      })
      .catch(() => setUser(null))
  }, [])

  const logout = () => {
    fetch(`${API}/auth/logout`, { method: 'POST' }).finally(() => {
      setUser(null); setView(null); setZones(null); setMapReady(false)
    })
  }

  // Map lives only in the zoning view; init once the user (an actuary/admin) is in.
  useEffect(() => {
    if (!user || !canZone || !mapContainer.current || mapRef.current) return
    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: 'https://tiles.openfreemap.org/styles/positron',
      center: [37.9, 0.2], // Kenya
      zoom: 5,
    })
    map.addControl(new maplibregl.NavigationControl())
    map.on('load', () => setMapReady(true))
    mapRef.current = map
    const observer = new ResizeObserver(() => map.resize())
    observer.observe(mapContainer.current)
    return () => {
      observer.disconnect()
      map.remove()
      mapRef.current = null
    }
  }, [user, canZone])

  useEffect(() => {
    if (!user) return
    const poll = () =>
      fetch(`${API}/health`)
        .then((r) => r.json())
        .then(setHealth)
        .catch(() => setHealth({}))
    poll()
    const t = setInterval(poll, 10000)
    return () => clearInterval(t)
  }, [user])

  // Draw (or clear) zone polygons whenever a zoning run is selected.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return
    const cleanup = () => {
      if (map.getLayer('zones-fill')) map.removeLayer('zones-fill')
      if (map.getLayer('zones-line')) map.removeLayer('zones-line')
      if (map.getSource('zones')) map.removeSource('zones')
    }
    cleanup()
    if (!zones) return

    const coloured = {
      ...zones,
      features: zones.features.map((f) => ({
        ...f,
        properties: {
          ...f.properties,
          colour:
            f.properties.zone != null
              ? PALETTE[(f.properties.zone - 1) % PALETTE.length]
              : 'rgba(0,0,0,0)',
        },
      })),
    }
    map.addSource('zones', { type: 'geojson', data: coloured })
    map.addLayer({
      id: 'zones-fill',
      type: 'fill',
      source: 'zones',
      paint: { 'fill-color': ['get', 'colour'], 'fill-opacity': 0.55 },
    })
    map.addLayer({
      id: 'zones-line',
      type: 'line',
      source: 'zones',
      paint: { 'line-color': '#333', 'line-width': 0.6 },
    })

    const onClick = (e) => {
      const p = e.features?.[0]?.properties
      if (!p) return
      new maplibregl.Popup()
        .setLngLat(e.lngLat)
        .setHTML(
          `<strong>Zone ${p.zone ?? '—'}</strong>${
            p.district ? `<br/>${p.district}` : ''
          }<br/>${p.pixels} pixels<br/>homogeneity: ${
            p.homogeneity != null ? Number(p.homogeneity).toFixed(3) : 'n/a (needs ≥3 years)'
          }`,
        )
        .addTo(map)
    }
    map.on('click', 'zones-fill', onClick)
    return () => {
      map.off('click', 'zones-fill', onClick)
      cleanup()
    }
  }, [zones, mapReady])

  if (user === undefined) return <div className="app-loading">Loading…</div>
  if (user === null) return <Login onLogin={(u) => {
    setUser(u)
    const first = TABS.find((t) => u.role === 'admin' || t.roles.includes(u.role))
    setView(first ? first.key : 'none')
  }} />

  return (
    <div className="app">
      <header className="topbar">
        <h1>Mainstay</h1>
        <nav className="tabs">
          {tabs.map((t) => (
            <button key={t.key} className={view === t.key ? 'on' : ''} onClick={() => setView(t.key)}>
              {t.label}
            </button>
          ))}
        </nav>
        <div className="status">
          <StatusDot label="api" state={health.api} />
          <StatusDot label="db" state={health.db} />
          <StatusDot label="worker" state={health.worker} />
          <span className="user-chip">
            {user.username} <em>{user.role}</em>
            <button className="logout" onClick={logout}>Sign out</button>
          </span>
        </div>
      </header>

      {canZone && (
        <div className="body" style={{ display: view === 'zoning' ? 'flex' : 'none' }}>
          <aside className="sidebar">
            <WeatherPanel country={country} onCountry={setCountry} />
            <hr className="divider" />
            <ZoningPanel country={country} onZones={setZones} />
          </aside>
          <div ref={mapContainer} className="map" />
        </div>
      )}
      {view === 'crops' && <CropLibrary />}
      {view === 'products' && <ProductDesign />}
      {view === 'ops' && <OperationsPanel />}
      {view === 'users' && <UsersPanel />}
      {view === 'none' && (
        <div className="empty-role">
          <p>Field agents quote from the mobile app.</p>
          <a href={`${API}/agent`} target="_blank" rel="noreferrer">Open the agent quoting page →</a>
        </div>
      )}
    </div>
  )
}
