import { useEffect, useState } from 'react'

import { API } from './config.js'

// A phase's settlement status → badge label + class (extends .pstatus in css).
const STATUS_LABEL = { upcoming: 'upcoming', provisional: 'provisional', settled: 'settled' }
const money = (n) => (n == null ? '—' : Math.round(n).toLocaleString())

export default function SeasonDashboard() {
  const [products, setProducts] = useState([])
  const [productId, setProductId] = useState('')
  const [view, setView] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetch(`${API}/products/published`).then((r) => r.json()).then((ps) => {
      setProducts(ps)
      if (ps.length && !productId) setProductId(ps[0].id)
    }).catch(() => {})
  }, []) // eslint-disable-line

  const load = () => {
    if (!productId) return
    fetch(`${API}/settlement/season?product_id=${encodeURIComponent(productId)}`)
      .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); return r.json() })
      .then((v) => { setView(v); setError(null) })
      .catch((e) => { setView(null); setError(e.message) })
  }
  useEffect(() => { load() }, [productId]) // eslint-disable-line

  // Run the settlement sweep on demand (the scheduled job runs the same code).
  const recompute = () => {
    setBusy(true); setError(null)
    fetch(`${API}/settlement/run`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_id: productId }),
    })
      .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); return r.json() })
      .then(() => load())
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false))
  }

  const Badge = ({ s }) => <span className={`pstatus ${s}`}>{STATUS_LABEL[s] || s}</span>

  return (
    <div className="crop-view">
      <div className="crop-list" style={{ width: 320 }}>
        <h2>Season dashboard</h2>
        <p className="hint-note">
          The season watched live through the same engine that priced the product.
          Provisional estimates come from the in-season window; settled values are
          CHIRPS final, past the ~3-week lag — only those are ever paid.
        </p>
        <label>Product
          <select value={productId} onChange={(e) => setProductId(e.target.value)}>
            {[...new Map(products.map((p) => [p.id, p])).values()].map((p) => (
              <option key={p.id} value={p.id}>{p.crop} · {p.season.replace('_', ' ')} ({p.country})</option>
            ))}
          </select>
        </label>

        {view && (
          <div className="cached">
            <h3>This season</h3>
            <p className="cached-p">
              {view.crop} · {view.season.replace('_', ' ')} · {view.season_year} · {view.country}<br />
              Sum insured {money(view.sum_insured)} · final lag {view.final_lag_days} days<br />
              As of {view.as_of}
            </p>
            <div className="loading-actions" style={{ marginTop: '0.6rem' }}>
              <button className="secondary" disabled={busy} onClick={recompute}>
                {busy ? 'Settling…' : 'Recompute settlement'}
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="crop-edit">
        {error && <div className="error">{error}</div>}
        {!view ? (
          <p className="cached">Select a published product to watch its season.</p>
        ) : view.zones.length === 0 ? (
          <p className="cached">This product has no zones.</p>
        ) : (
          view.zones.map((z) => (
            <div key={z.zone} style={{ marginBottom: '1.6rem' }}>
              <h3 style={{ marginBottom: '0.2rem' }}>
                Zone {z.zone}
                <span className="hint-note" style={{ marginLeft: '0.6rem', fontStyle: 'normal' }}>
                  {z.policies} farmer{z.policies === 1 ? '' : 's'} bound ·
                  settled payout {money(z.settled_payout)}
                  {z.provisional_payout > 0 ? ` · provisional ${money(z.provisional_payout)}` : ''}
                </span>
              </h3>
              <table className="stages" style={{ maxWidth: 820 }}>
                <thead>
                  <tr>
                    <th>Phase</th><th>Cover</th><th>Window</th>
                    <th>Index</th><th>Payout</th><th>Status</th><th>Final data</th>
                  </tr>
                </thead>
                <tbody>
                  {z.phases.map((p) => (
                    <tr key={p.phase}>
                      <td><strong>{p.phase.replace('_', ' ')}</strong></td>
                      <td className="normal">{p.cover_type.replace('_', ' ')}</td>
                      <td className="normal">{p.window_start} → {p.window_end}</td>
                      <td>{p.index == null ? '—' : p.index.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                      <td className={p.status === 'settled' && p.payout ? 'paid' : ''}>{money(p.payout)}</td>
                      <td><Badge s={p.status} /></td>
                      <td className="normal">{p.status === 'settled' ? 'confirmed' : p.final_ready}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
