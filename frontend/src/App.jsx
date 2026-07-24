import { useEffect, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import WeatherPanel from './WeatherPanel.jsx'
import ZoningPanel from './ZoningPanel.jsx'
import CropLibrary from './CropLibrary.jsx'

const API = 'http://localhost:8000'

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
  const [view, setView] = useState('zoning')

  useEffect(() => {
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
    }
  }, [])

  useEffect(() => {
    const poll = () =>
      fetch(`${API}/health`)
        .then((r) => r.json())
        .then(setHealth)
        .catch(() => setHealth({}))
    poll()
    const t = setInterval(poll, 10000)
    return () => clearInterval(t)
  }, [])

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

  return (
    <div className="app">
      <header className="topbar">
        <h1>AEZ Creator &amp; Weather Index Insurance Platform</h1>
        <nav className="tabs">
          <button className={view === 'zoning' ? 'on' : ''} onClick={() => setView('zoning')}>
            Zoning
          </button>
          <button className={view === 'crops' ? 'on' : ''} onClick={() => setView('crops')}>
            Crop library
          </button>
        </nav>
        <div className="status">
          <StatusDot label="api" state={health.api} />
          <StatusDot label="db" state={health.db} />
          <StatusDot label="worker" state={health.worker} />
        </div>
      </header>
      <div className="body" style={{ display: view === 'zoning' ? 'flex' : 'none' }}>
        <aside className="sidebar">
          <WeatherPanel country={country} onCountry={setCountry} />
          <hr className="divider" />
          <ZoningPanel country={country} onZones={setZones} />
        </aside>
        <div ref={mapContainer} className="map" />
      </div>
      {view === 'crops' && <CropLibrary />}
    </div>
  )
}
