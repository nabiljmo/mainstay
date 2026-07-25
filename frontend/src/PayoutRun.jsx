import { useEffect, useState } from 'react'

const API = 'http://localhost:8000'
const money = (n) => (n == null ? '—' : Math.round(n).toLocaleString())

export default function PayoutRun() {
  const [products, setProducts] = useState([])
  const [productId, setProductId] = useState('')
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetch(`${API}/products/published`).then((r) => r.json()).then((ps) => {
      setProducts(ps)
      if (ps.length && !productId) setProductId(ps[0].id)
    }).catch(() => {})
  }, []) // eslint-disable-line

  const load = () => {
    if (!productId) return
    setConfirming(false)
    fetch(`${API}/payouts/run?product_id=${encodeURIComponent(productId)}`)
      .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); return r.json() })
      .then((v) => { setRun(v); setError(null) })
      .catch((e) => { setRun(null); setError(e.message) })
  }
  useEffect(() => { load() }, [productId]) // eslint-disable-line

  const release = () => {
    setBusy(true); setError(null)
    fetch(`${API}/payouts/release`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_id: productId, confirm: true }),
    })
      .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); return r.json() })
      .then(() => load())
      .catch((e) => setError(e.message))
      .finally(() => { setBusy(false); setConfirming(false) })
  }

  // Fetch the file as a blob (cookie auth) and trigger a download.
  const download = () => {
    fetch(`${API}/payouts/runs/${run.run_id}/file`)
      .then((r) => r.blob())
      .then((blob) => {
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url; a.download = `${run.run_id}.csv`
        document.body.appendChild(a); a.click(); a.remove()
        URL.revokeObjectURL(url)
      })
      .catch((e) => setError(e.message))
  }

  const released = run && run.status === 'released'

  return (
    <div className="crop-view">
      <div className="crop-list" style={{ width: 320 }}>
        <h2>Payout run</h2>
        <p className="hint-note">
          Season close. Review the settled season, then one click releases the
          payout file to the payment rails. Release locks the run and marks every
          policy settled. Money is recorded here, never moved.
        </p>
        <label>Product
          <select value={productId} onChange={(e) => setProductId(e.target.value)}>
            {[...new Map(products.map((p) => [p.id, p])).values()].map((p) => (
              <option key={p.id} value={p.id}>{p.crop} · {p.season.replace('_', ' ')} ({p.country})</option>
            ))}
          </select>
        </label>

        {run && (
          <div className="cached">
            <h3>{run.season} · {run.season_year}</h3>
            <p>
              {released
                ? <>Released by {run.released_by}<br />{run.released_at}</>
                : run.ready
                  ? 'Settled and ready to release.'
                  : 'Not ready — some phases are still unsettled.'}
            </p>
            <p style={{ marginTop: '0.5rem' }}>
              <strong>{money(run.total_amount)}</strong> to <strong>{run.farmer_count}</strong> farmer{run.farmer_count === 1 ? '' : 's'}
              <br />(of {run.total_farmers} bound)
            </p>
          </div>
        )}
      </div>

      <div className="crop-edit">
        {error && <div className="error">{error}</div>}
        {!run ? (
          <p className="cached">Select a published product to review its payout run.</p>
        ) : (
          <>
            <h2>
              {run.crop} · {run.season.replace('_', ' ')} · {run.season_year}
              {released && <span className="pstatus settled" style={{ marginLeft: '0.6rem' }}>released</span>}
            </h2>

            {!run.ready && !released && (
              <div className="error" style={{ background: 'var(--warn-soft)', color: '#92400e', borderColor: 'var(--warn-border)' }}>
                Not ready to close: {run.pending.map((p) => `zone ${p.zone} (${p.settled}/${p.total} phases settled)`).join(', ')}.
              </div>
            )}
            {run.anomalies.length > 0 && (
              <div className="error">
                ⚠ Anomaly: {run.anomalies.map((a) => `zone ${a.zone} pays ${a.multiple}× its expected loss`).join('; ')}. Review before releasing.
              </div>
            )}

            <h3>Per zone</h3>
            <table className="stages" style={{ maxWidth: 820 }}>
              <thead>
                <tr>
                  <th>Zone</th><th>Farmers</th><th>Settled payout</th>
                  <th>Expected loss</th><th>Zone payout</th><th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {run.zones.map((z) => (
                  <tr key={z.zone}>
                    <td><strong>{z.zone}</strong>{z.anomaly && <i className="flag red" title="pays >3× expected loss" />}</td>
                    <td className="normal">{z.farmers}</td>
                    <td>{money(z.settled_payout)}</td>
                    <td className="normal">{money(z.expected_loss)}</td>
                    <td className={z.zone_payout > 0 ? 'paid' : ''}>{money(z.zone_payout)}</td>
                    <td className="normal">{z.evidence_ref}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {run.largest.length > 0 && (
              <>
                <h3 style={{ marginTop: '1.2rem' }}>Largest payments</h3>
                <table className="stages" style={{ maxWidth: 520 }}>
                  <thead><tr><th>Policy</th><th>Zone</th><th>Amount</th></tr></thead>
                  <tbody>
                    {run.largest.map((l, i) => (
                      <tr key={i}>
                        <td className="normal">{l.policy_id}</td>
                        <td className="normal">{l.zone}</td>
                        <td className="paid">{money(l.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}

            <div className="loading-actions" style={{ marginTop: '1.2rem' }}>
              {released ? (
                <button className="secondary" onClick={download}>Download payout file</button>
              ) : !confirming ? (
                <button className="secondary" disabled={!run.ready} onClick={() => setConfirming(true)}>
                  Release payout file
                </button>
              ) : (
                <>
                  <span className="hint-note" style={{ fontStyle: 'normal' }}>
                    Release {money(run.total_amount)} to {run.farmer_count} farmer{run.farmer_count === 1 ? '' : 's'}? This locks the season.
                  </span>
                  <button className="secondary" disabled={busy} onClick={release}>
                    {busy ? 'Releasing…' : 'Yes, release'}
                  </button>
                  <button className="secondary" disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
