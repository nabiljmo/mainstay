import { useEffect, useState } from 'react'

const API = 'http://localhost:8000'
const STATUS = ['', 'draft', 'active', 'expired', 'settled']

export default function PoliciesPanel() {
  const [products, setProducts] = useState([])
  const [filters, setFilters] = useState({ product_id: '', status: '', zone: '', partner: '' })
  const [policies, setPolicies] = useState([])
  const [openId, setOpenId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`${API}/products/published`).then((r) => r.json()).then(setProducts).catch(() => {})
  }, [])

  const load = () => {
    const qs = new URLSearchParams(
      Object.entries(filters).filter(([, v]) => v !== '' && v != null),
    ).toString()
    fetch(`${API}/policies${qs ? `?${qs}` : ''}`).then((r) => r.json()).then(setPolicies).catch(() => {})
  }
  useEffect(() => { load() }, [filters]) // eslint-disable-line

  const open = (id) => {
    setOpenId(id)
    fetch(`${API}/policies/${id}`).then((r) => r.json()).then(setDetail).catch(() => {})
  }

  const setStatus = (id, status) => {
    setError(null)
    fetch(`${API}/policies/${id}/status`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
    })
      .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); return r.json() })
      .then(() => { open(id); load() })
      .catch((e) => setError(e.message))
  }

  const Badge = ({ s }) => <span className={`pstatus ${s}`}>{s}</span>

  return (
    <div className="crop-view">
      <div className="crop-list" style={{ width: 320 }}>
        <h2>Policy register</h2>
        <label>Product
          <select value={filters.product_id} onChange={(e) => setFilters({ ...filters, product_id: e.target.value })}>
            <option value="">all products</option>
            {[...new Map(products.map((p) => [p.id, p])).values()].map((p) => (
              <option key={p.id} value={p.id}>{p.crop} · {p.season.replace('_', ' ')} ({p.country})</option>
            ))}
          </select>
        </label>
        <label>Status
          <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
            {STATUS.map((s) => <option key={s} value={s}>{s || 'any status'}</option>)}
          </select>
        </label>
        <label>Zone
          <input type="number" placeholder="any" value={filters.zone} onChange={(e) => setFilters({ ...filters, zone: e.target.value })} />
        </label>
        <label>Partner
          <input placeholder="name contains…" value={filters.partner} onChange={(e) => setFilters({ ...filters, partner: e.target.value })} />
        </label>

        <h3 style={{ marginTop: '1.25rem' }}>{policies.length} policies</h3>
        {policies.map((m) => (
          <div key={m.id} className={`run ${openId === m.id ? 'selected' : ''}`} onClick={() => open(m.id)}>
            <strong>{m.partner_name || 'Individual sale'}</strong> <Badge s={m.status} /><br />
            {m.crop} · {m.season.replace('_', ' ')} · {m.farmers} farmer{m.farmers === 1 ? '' : 's'} · prem {m.total_premium.toLocaleString()}
          </div>
        ))}
      </div>

      <div className="crop-edit">
        {!detail ? (
          <p className="cached">Select a policy to see its schedule.</p>
        ) : (
          <>
            <h2>{detail.partner_name || 'Individual sale'} <Badge s={detail.status} /></h2>
            <p className="hint-note">
              {detail.id} · {detail.crop} · {detail.season.replace('_', ' ')} · sold by {detail.created_by}
              {detail.receipt_ref ? ` · receipt ${detail.receipt_ref} (${detail.receipt_date})` : ' · no receipt yet'}
            </p>

            <div className="loading-actions" style={{ margin: '0.5rem 0 1rem' }}>
              {detail.status === 'active' && (
                <>
                  <button className="secondary" onClick={() => setStatus(detail.id, 'settled')}>Mark settled</button>
                  <button className="secondary" onClick={() => setStatus(detail.id, 'expired')}>Mark expired</button>
                </>
              )}
              {detail.status === 'draft' && (
                <button className="secondary" onClick={() => setStatus(detail.id, 'expired')}>Mark lapsed</button>
              )}
            </div>
            {error && <div className="error">{error}</div>}

            <h3>Schedule of farmers</h3>
            <table className="stages" style={{ maxWidth: 760 }}>
              <thead><tr><th>Farmer</th><th>Phone</th><th>Gender</th><th>National ID</th><th>Zone</th><th>Sum insured</th><th>Premium</th></tr></thead>
              <tbody>
                {detail.schedule.map((s) => (
                  <tr key={s.id}>
                    <td><strong>{s.farmer.name}</strong></td>
                    <td>{s.farmer.phone}</td>
                    <td>{s.farmer.gender || '—'}</td>
                    <td>{s.farmer.national_id || '—'}</td>
                    <td>{s.zone}</td>
                    <td>{s.sum_insured.toLocaleString()}</td>
                    <td>{s.premium.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="hint-note">Total premium {detail.total_premium.toLocaleString()} on {detail.total_sum_insured.toLocaleString()} of cover. Farmer details are decrypted for your role only.</p>
          </>
        )}
      </div>
    </div>
  )
}
