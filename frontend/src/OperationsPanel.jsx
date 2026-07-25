import { useEffect, useState } from 'react'

const API = 'http://localhost:8000'

export default function OperationsPanel() {
  const [signals, setSignals] = useState([])

  useEffect(() => {
    fetch(`${API}/demand-signals`).then((r) => r.json()).then(setSignals).catch(() => {})
  }, [])

  const REASON = { no_product: 'No product yet', outside_coverage: 'Outside mapped area' }

  return (
    <div className="crop-edit" style={{ overflowY: 'auto' }}>
      <h2>Demand signals</h2>
      <p className="hint-note">Where agents asked for cover we couldn’t quote — the map for what to build next.</p>
      {signals.length === 0 ? (
        <p className="cached">No demand signals yet.</p>
      ) : (
        <table className="stages" style={{ maxWidth: 720, marginTop: '0.75rem' }}>
          <thead><tr><th>When</th><th>Crop · season</th><th>Where</th><th>Reason</th><th>By</th></tr></thead>
          <tbody>
            {signals.map((s) => (
              <tr key={s.id}>
                <td className="normal">{s.created_at.slice(0, 16).replace('T', ' ')}</td>
                <td>{s.crop} · {s.season.replace('_', ' ')}</td>
                <td>{s.admin_area || `${s.lat?.toFixed?.(2)}, ${s.lon?.toFixed?.(2)}`}</td>
                <td>{REASON[s.reason] || s.reason}</td>
                <td className="normal">{s.created_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
