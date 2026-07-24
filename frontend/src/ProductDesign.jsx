import { useEffect, useState } from 'react'

const API = 'http://localhost:8000'

export default function ProductDesign() {
  const [maps, setMaps] = useState([])
  const [crops, setCrops] = useState([])
  const [drafts, setDrafts] = useState([])
  const [form, setForm] = useState({
    zone_map: '', crop: 'maize', crop_version: 1, season: 'long_rains',
    start_year: 2021, end_year: 2025, sum_insured: 10000,
  })
  const [job, setJob] = useState(null)
  const [openDraft, setOpenDraft] = useState(null) // {id, definition}
  const [zone, setZone] = useState(null)
  const [phases, setPhases] = useState([])
  const [pricing, setPricing] = useState(null)
  const [error, setError] = useState(null)

  const loadDrafts = () => fetch(`${API}/products/drafts`).then((r) => r.json()).then(setDrafts).catch(() => {})

  useEffect(() => {
    fetch(`${API}/zone-maps?country=KEN`).then((r) => r.json()).then((m) => {
      setMaps(m)
      if (m.length) setForm((f) => ({ ...f, zone_map: m[0].name }))
    })
    fetch(`${API}/crops`).then((r) => r.json()).then(setCrops)
    loadDrafts()
  }, [])

  useEffect(() => {
    if (!job?.id || job.state === 'SUCCESS' || job.state === 'FAILURE') return
    const t = setInterval(() => {
      fetch(`${API}/jobs/${job.id}`).then((r) => r.json()).then((s) => {
        setJob({ id: job.id, state: s.state, progress: s.progress, result: s.result })
        if (s.state === 'SUCCESS') { loadDrafts(); if (s.result?.draft_id) open(s.result.draft_id) }
      })
    }, 1500)
    return () => clearInterval(t)
  }, [job?.id, job?.state])

  const createDraft = () => {
    setError(null)
    fetch(`${API}/products/draft`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form),
    })
      .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); return r.json() })
      .then((d) => setJob({ id: d.job_id, state: 'PENDING' }))
      .catch((e) => setError(e.message))
  }

  const open = (id) => {
    fetch(`${API}/products/drafts/${id}`).then((r) => r.json()).then((def) => {
      setOpenDraft({ id, definition: def })
      const firstZone = Object.keys(def.zones)[0]
      selectZone(id, def, firstZone)
    })
  }

  const selectZone = (id, def, z) => {
    setZone(z)
    const p = JSON.parse(JSON.stringify(def.zones[z].phases))
    setPhases(p)
    price(id, z, p)
  }

  const price = (id, z, p) => {
    fetch(`${API}/products/drafts/${id}/zones/${z}/price`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ phases: p }),
    }).then((r) => r.json()).then(setPricing).catch(() => {})
  }

  const editPhase = (i, field, value) => {
    const p = [...phases]
    p[i] = { ...p[i], [field]: field === 'cover_type' ? value : Number(value) }
    setPhases(p)
  }

  return (
    <div className="crop-view">
      <div className="crop-list">
        <h2>New product</h2>
        <label>Zone map
          <select value={form.zone_map} onChange={(e) => setForm({ ...form, zone_map: e.target.value })}>
            {maps.map((m) => <option key={m.name} value={m.name}>{m.name} ({m.params.n_clusters}z)</option>)}
          </select>
        </label>
        <label>Crop
          <select value={form.crop} onChange={(e) => setForm({ ...form, crop: e.target.value })}>
            {crops.map((c) => <option key={c.crop} value={c.crop}>{c.crop} v{c.version}</option>)}
          </select>
        </label>
        <label>Season
          <select value={form.season} onChange={(e) => setForm({ ...form, season: e.target.value })}>
            <option value="long_rains">long_rains</option>
            <option value="short_rains">short_rains</option>
          </select>
        </label>
        <label>Sum insured
          <input type="number" value={form.sum_insured} onChange={(e) => setForm({ ...form, sum_insured: +e.target.value })} />
        </label>
        <button onClick={createDraft} disabled={job && job.state !== 'SUCCESS' && job.state !== 'FAILURE'}>
          {job && job.state !== 'SUCCESS' && job.state !== 'FAILURE' ? `Drafting… ${job.progress?.stage ?? ''}` : 'Draft product'}
        </button>
        {error && <div className="error">{error}</div>}

        <h3 style={{ marginTop: '1.25rem' }}>Drafts</h3>
        {drafts.map((d) => (
          <div key={d.id} className={`run ${openDraft?.id === d.id ? 'selected' : ''}`} onClick={() => open(d.id)}>
            <strong>{d.crop} · {d.season}</strong><br />
            {d.zone_map} · {d.years.length}yr · SI {d.sum_insured}
          </div>
        ))}
      </div>

      {openDraft && zone && (
        <div className="crop-edit">
          <h2>{openDraft.definition.zones && `${Object.keys(openDraft.definition.zones).length} zones`} · plant {openDraft.definition.plant_start}</h2>
          <label style={{ maxWidth: 200 }}>Zone
            <select value={zone} onChange={(e) => selectZone(openDraft.id, openDraft.definition, e.target.value)}>
              {Object.keys(openDraft.definition.zones).map((z) => <option key={z} value={z}>Zone {z}</option>)}
            </select>
          </label>

          <h3>Phases (editable)</h3>
          <table className="stages">
            <thead><tr><th>Phase</th><th>Cover</th><th>Strike</th><th>Exit</th><th>Limit</th></tr></thead>
            <tbody>
              {phases.map((p, i) => (
                <tr key={i}>
                  <td>{p.name}</td>
                  <td>
                    <select value={p.cover_type} onChange={(e) => editPhase(i, 'cover_type', e.target.value)}>
                      <option value="deficit">deficit</option>
                      <option value="excess">excess</option>
                      <option value="dry_spell">dry_spell</option>
                    </select>
                  </td>
                  <td><input type="number" value={p.strike} onChange={(e) => editPhase(i, 'strike', e.target.value)} /></td>
                  <td><input type="number" value={p.exit} onChange={(e) => editPhase(i, 'exit', e.target.value)} /></td>
                  <td><input type="number" value={p.limit} onChange={(e) => editPhase(i, 'limit', e.target.value)} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <button onClick={() => price(openDraft.id, zone, phases)}>Recompute payouts</button>

          {pricing && (
            <>
              <h3>Historical payouts — burning cost {pricing.burning_cost} of {openDraft.definition.sum_insured}
                {' '}({(100 * pricing.burning_cost / openDraft.definition.sum_insured).toFixed(1)}%)</h3>
              <table className="stages">
                <thead><tr><th>Year</th>{phases.map((p) => <th key={p.name}>{p.name.slice(0, 5)}</th>)}<th>Total</th></tr></thead>
                <tbody>
                  {pricing.years.map((y) => (
                    <tr key={y.year}>
                      <td>{y.year}</td>
                      {y.phases.map((ph, i) => <td key={i}>{ph.payout.toFixed(0)}</td>)}
                      <td><strong>{y.total_payout.toFixed(0)}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </div>
  )
}
