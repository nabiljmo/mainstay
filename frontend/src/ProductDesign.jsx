import { useEffect, useState } from 'react'

import { API, errText } from './config.js'

function QQPlot({ qq }) {
  const W = 260, H = 160, pad = 30
  const all = qq.flatMap((p) => [p.actual, p.theoretical])
  const lo = Math.min(...all), hi = Math.max(...all)
  const sx = (v) => pad + ((v - lo) / (hi - lo || 1)) * (W - 2 * pad)
  const sy = (v) => H - pad - ((v - lo) / (hi - lo || 1)) * (H - 2 * pad)
  return (
    <div className="qq">
      <svg width={W} height={H}>
        <line x1={sx(lo)} y1={sy(lo)} x2={sx(hi)} y2={sy(hi)} stroke="#9ca3af" strokeDasharray="3 3" />
        {qq.map((p, i) => (
          <circle key={i} cx={sx(p.theoretical)} cy={sy(p.actual)} r="3.5" fill="#16a34a" />
        ))}
        <text x={W / 2} y={H - 4} textAnchor="middle" fontSize="9" fill="#6b7280">fitted quantile</text>
        <text x="8" y={H / 2} fontSize="9" fill="#6b7280" transform={`rotate(-90 8 ${H / 2})`} textAnchor="middle">actual</text>
      </svg>
      <span className="qq-note">Points near the dashed line mean the fit is good.</span>
    </div>
  )
}

export default function ProductDesign() {
  const [maps, setMaps] = useState([])
  const [crops, setCrops] = useState([])
  const [drafts, setDrafts] = useState([])
  const [form, setForm] = useState({
    zone_map: '', country: 'KEN', crop: 'maize', crop_version: 1, season: 'long_rains',
    start_year: 2021, end_year: 2025, sum_insured: 10000,
  })
  const [job, setJob] = useState(null)
  const [openDraft, setOpenDraft] = useState(null) // {id, definition}
  const [zone, setZone] = useState(null)
  const [phases, setPhases] = useState([])
  const [pricing, setPricing] = useState(null)
  const [error, setError] = useState(null)
  const [mode, setMode] = useState('percent') // 'percent' | 'absolute'
  const [tip, setTip] = useState(null) // { text, x, y }
  const [distribution, setDistribution] = useState('gamma')
  const [loadings, setLoadings] = useState(null)
  const [econ, setEcon] = useState(null)
  const [computing, setComputing] = useState(false)
  const [edits, setEdits] = useState({}) // {zone: phases} — terms the actuary settled per zone
  const [publishing, setPublishing] = useState(false)
  const [published, setPublished] = useState(null)

  const loadDrafts = () => fetch(`${API}/products/drafts`).then((r) => r.json()).then(setDrafts).catch(() => {})

  useEffect(() => {
    fetch(`${API}/zone-maps?country=KEN`).then((r) => r.json()).then((m) => {
      setMaps(m)
      if (m.length) setForm((f) => ({ ...f, zone_map: m[0].name, country: m[0].country || f.country }))
    })
    fetch(`${API}/crops`).then((r) => r.json()).then((cs) => {
      setCrops(cs)
      if (cs.length) setForm((f) => ({ ...f, crop: cs[0].crop, crop_version: cs[0].version }))
    })
    fetch(`${API}/pricing/defaults`).then((r) => r.json()).then((d) => setLoadings(d.loadings))
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
      .then(async (r) => { if (!r.ok) throw new Error(errText((await r.json()).detail)); return r.json() })
      .then((d) => setJob({ id: d.job_id, state: 'PENDING' }))
      .catch((e) => setError(e.message))
  }

  const open = (id) => {
    fetch(`${API}/products/drafts/${id}`).then((r) => r.json()).then((def) => {
      setOpenDraft({ id, definition: def })
      setEdits({})
      setPublished(null)
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

  // One call computes payouts and pricing together (and the backend caches
  // the rainfall series per draft, so repeat computes are near-instant).
  const price = (id, z, p, distOverride) => {
    if (!loadings) return
    const withMode = p.map((ph) => ({ ...ph, trigger_mode: mode }))
    setComputing(true)
    setError(null)
    fetch(`${API}/products/drafts/${id}/zones/${z}/economics`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phases: withMode, distribution: distOverride || distribution, loadings }),
    })
      .then(async (r) => {
        if (!r.ok) {
          const body = await r.json().catch(() => ({}))
          throw new Error(body.detail || `Pricing failed (${r.status})`)
        }
        return r.json()
      })
      .then((d) => {
        setEcon(d)
        setPricing({
          years: d.years,
          burning_cost: d.burning_cost,
          burning_cost_explanation: d.burning_cost_explanation,
          phase_meanings: d.phase_meanings,
          sum_insured: d.sum_insured,
        })
        // Remember the settled terms for this zone so publishing freezes the
        // actuary's edits, not just the original proposal.
        setEdits((prev) => ({ ...prev, [z]: withMode }))
      })
      .catch((e) => setError(e.message))
      .finally(() => setComputing(false))
  }

  const priceEconomics = (id, z, p, distOverride) => price(id, z, p, distOverride)

  const publishProduct = () => {
    if (!openDraft) return
    const nZones = Object.keys(openDraft.definition.zones).length
    const reviewed = Object.keys(edits).length
    const ok = window.confirm(
      `Publish this product?\n\nThis freezes a read-only version across all ${nZones} zones ` +
      `(${reviewed} reviewed this session) and makes it the quoting source. ` +
      `Further changes require a new version.`,
    )
    if (!ok) return
    setPublishing(true)
    setError(null)
    fetch(`${API}/products/drafts/${openDraft.id}/publish`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ distribution, loadings, zone_phases: edits, published_by: 'admin' }),
    })
      .then(async (r) => {
        if (!r.ok) { const b = await r.json().catch(() => ({})); throw new Error(b.detail || `Publish failed (${r.status})`) }
        return r.json()
      })
      .then(setPublished)
      .catch((e) => setError(e.message))
      .finally(() => setPublishing(false))
  }

  const editLoading = (i, field, value) => {
    const l = [...loadings]
    l[i] = { ...l[i], [field]: field === 'value' ? Number(value) : value }
    setLoadings(l)
  }
  const addLoading = () => setLoadings([...loadings, { name: 'New loading', basis: 'pct_gross', value: 5 }])
  const removeLoading = (i) => setLoadings(loadings.filter((_, j) => j !== i))

  const editPhase = (i, field, value) => {
    const p = [...phases]
    p[i] = { ...p[i], [field]: field === 'cover_type' ? value : Number(value) }
    setPhases(p)
  }

  // Scale the stage limits so they add up exactly to the sum insured, keeping
  // the actuary's chosen proportions.
  const balanceLimits = () => {
    const si = openDraft?.definition?.sum_insured
    const total = phases.reduce((s, p) => s + Number(p.limit || 0), 0)
    if (!si || !total) return
    setPhases(phases.map((p) => ({ ...p, limit: Math.round((Number(p.limit) / total) * si * 100) / 100 })))
  }

  // "Cover these stages only": tick a stage off to drop its cover (limit -> 0),
  // and the remaining stages rebalance to the full sum insured. Ticking one back
  // on restores its original proposed share. Zeroed stages carry no premium, so
  // this is how you build a cheaper flowering-only (or germination-only) variant.
  const toggleStage = (i) => {
    const si = openDraft?.definition?.sum_insured || 0
    const next = phases.map((p) => ({ ...p }))
    if (Number(next[i].limit) > 0) {
      next[i].limit = 0
    } else {
      const original = openDraft?.definition?.zones?.[zone]?.phases?.[i]?.limit
      const nowCovered = next.filter((p, j) => j === i || Number(p.limit) > 0).length
      next[i].limit = original && original > 0 ? original : (si / (nowCovered || 1))
    }
    const total = next.reduce((s, p) => s + Number(p.limit || 0), 0)
    setPhases(
      total && si
        ? next.map((p) => ({ ...p, limit: Number(p.limit) > 0 ? Math.round((Number(p.limit) / total) * si * 100) / 100 : 0 }))
        : next,
    )
  }

  // Phases sent to the backend carry the current trigger mode so it resolves
  // percentages against each phase's stored reference (normal).
  const phasesForPricing = () => phases.map((p) => ({ ...p, trigger_mode: mode }))

  const showTip = (text) => (e) => setTip({ text, x: e.clientX, y: e.clientY })
  const moveTip = (e) => setTip((t) => (t ? { ...t, x: e.clientX, y: e.clientY } : t))
  const hideTip = () => setTip(null)

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
          <select value={form.crop} onChange={(e) => {
            const c = crops.find((x) => x.crop === e.target.value)
            setForm({ ...form, crop: e.target.value, crop_version: c?.version ?? form.crop_version })
          }}>
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
          {econ && (
            <div className="premium-sticky">
              <div>
                <span className="ps-rate">{econ.price.premium_rate}%</span>
                <span className="ps-sub">rate · zone {zone}</span>
              </div>
              <div>
                <span className="ps-amt">{econ.price.gross_premium.toLocaleString()}</span>
                <span className="ps-sub">premium on {econ.sum_insured.toLocaleString()}</span>
              </div>
              <div>
                <span className="ps-amt">{econ.economics.technical_el.toLocaleString()}</span>
                <span className="ps-sub">expected loss</span>
              </div>
              <span className={`flag ${econ.quality_flag}`} title={`${econ.economics.n_years} years of data`} />
              <button className="publish-btn" onClick={publishProduct} disabled={publishing}>
                {publishing ? 'Publishing…' : 'Publish product'}
              </button>
            </div>
          )}

          {published && (
            <div className="published-banner">
              <div className="pb-head">
                <strong>Published {published.id}</strong>
                <span>version {published.version} · {published.n_zones} zones frozen · quoting source is now live</span>
              </div>
              <div className="pb-actions">
                <a href={`${API}/products/published/${published.id}/assumption-sheet`} target="_blank" rel="noreferrer">
                  Open assumption sheet (print → PDF)
                </a>
                <a href={`${API}/products/published/${published.id}`} target="_blank" rel="noreferrer">
                  View frozen product (JSON)
                </a>
              </div>
              <table className="stages pb-rates">
                <thead><tr><th>Zone</th><th>Rate</th><th>Gross premium</th><th>Expected loss</th></tr></thead>
                <tbody>
                  {published.rates.map((r) => (
                    <tr key={r.zone}>
                      <td>Zone {r.zone}</td>
                      <td><strong>{r.premium_rate}%</strong></td>
                      <td>{r.gross_premium.toLocaleString()}</td>
                      <td>{r.expected_loss.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h2>{openDraft.definition.zones && `${Object.keys(openDraft.definition.zones).length} zones`} · plant {openDraft.definition.plant_start}</h2>
          <label style={{ maxWidth: 200 }}>Zone
            <select value={zone} onChange={(e) => selectZone(openDraft.id, openDraft.definition, e.target.value)}>
              {Object.keys(openDraft.definition.zones).map((z) => <option key={z} value={z}>Zone {z}</option>)}
            </select>
          </label>

          <div className="trigger-mode">
            Triggers as:
            <button className={mode === 'percent' ? 'on' : ''} onClick={() => setMode('percent')}>
              % of normal
            </button>
            <button className={mode === 'absolute' ? 'on' : ''} onClick={() => setMode('absolute')}>
              absolute
            </button>
          </div>
          <h3>Phases (editable)</h3>
          <p className="hint-note" style={{ fontStyle: 'normal' }}>
            <strong>Deficit</strong> = drought, too little rain. <strong>Excess</strong> = flood, too much rain.
            <strong> Dry spell</strong> = a long unbroken run of dry days, even if the total was fine.
          </p>
          <table className="stages">
            <thead>
              <tr>
                <th>Phase</th><th>Cover</th><th>Normal</th>
                <th>Strike {mode === 'percent' ? '(%)' : ''}</th>
                <th>Exit {mode === 'percent' ? '(%)' : ''}</th>
                <th>Limit</th>
              </tr>
            </thead>
            <tbody>
              {phases.map((p, i) => (
                <tr key={i} style={{ opacity: Number(p.limit) > 0 ? 1 : 0.5 }}>
                  <td>
                    <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}>
                      <input type="checkbox" checked={Number(p.limit) > 0} onChange={() => toggleStage(i)} title="cover this stage" />
                      {p.name}
                    </label>
                  </td>
                  <td>
                    <select value={p.cover_type} onChange={(e) => editPhase(i, 'cover_type', e.target.value)}>
                      <option value="deficit">deficit</option>
                      <option value="excess">excess</option>
                      <option value="dry_spell">dry_spell</option>
                    </select>
                  </td>
                  <td className="normal">{p.reference}</td>
                  {mode === 'percent' && p.reference ? (
                    <>
                      <td>
                        <input type="number" value={p.strike_pct ?? ''} onChange={(e) => editPhase(i, 'strike_pct', e.target.value)} />
                        <span className="hint">{((p.strike_pct / 100) * p.reference).toFixed(1)}</span>
                      </td>
                      <td>
                        <input type="number" value={p.exit_pct ?? ''} onChange={(e) => editPhase(i, 'exit_pct', e.target.value)} />
                        <span className="hint">{((p.exit_pct / 100) * p.reference).toFixed(1)}</span>
                      </td>
                    </>
                  ) : (
                    <>
                      <td><input type="number" value={p.strike} onChange={(e) => editPhase(i, 'strike', e.target.value)} /></td>
                      <td><input type="number" value={p.exit} onChange={(e) => editPhase(i, 'exit', e.target.value)} /></td>
                    </>
                  )}
                  <td>
                    <input type="number" value={p.limit} onChange={(e) => editPhase(i, 'limit', e.target.value)} />
                    {openDraft.definition.sum_insured
                      ? <span className="hint">{Math.round((Number(p.limit) / openDraft.definition.sum_insured) * 100)}%</span>
                      : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {(() => {
            const si = openDraft.definition.sum_insured
            const total = phases.reduce((s, p) => s + Number(p.limit || 0), 0)
            const off = si && Math.abs(total - si) > Math.max(1, 0.005 * si)
            return (
              <p className="hint-note" style={{ fontStyle: 'normal', color: off ? '#b45309' : 'var(--text-muted)' }}>
                Cover split across stages: <strong>{Math.round(total).toLocaleString()}</strong> of {Number(si).toLocaleString()} sum insured.
                {off ? <> The stages don’t add up — <button className="secondary" style={{ marginLeft: '0.35rem', padding: '0.1rem 0.5rem' }} onClick={balanceLimits}>Balance to sum insured</button></> : ' ✓'}
              </p>
            )
          })()}
          <p className="hint-note">Untick a stage to drop its cover and rebalance onto the rest — that’s how you build a cheaper single-stage variant.</p>
          <button onClick={() => price(openDraft.id, zone, phases)} disabled={computing}>
            {computing ? 'Computing…' : 'Recompute payouts'}
          </button>
          {error && <div className="error">{error}</div>}

          {pricing && (
            <>
              {pricing.phase_meanings && (
                <div className="explainer">
                  <h3>What these settings mean</h3>
                  <ul>
                    {pricing.phase_meanings.map((m) => (
                      <li key={m.name}>{m.meaning}</li>
                    ))}
                  </ul>
                </div>
              )}

              <h3>Historical payouts</h3>
              <table className="stages">
                <thead><tr><th>Year</th>{phases.map((p) => <th key={p.name}>{p.name.slice(0, 5)}</th>)}<th>Total</th></tr></thead>
                <tbody>
                  {pricing.years.map((y) => (
                    <tr key={y.year}>
                      <td>{y.year}</td>
                      {y.phases.map((ph, i) => (
                        <td
                          key={i}
                          className={`tipcell ${ph.payout > 0 ? 'paid' : ''}`}
                          onMouseEnter={showTip(ph.why)}
                          onMouseMove={moveTip}
                          onMouseLeave={hideTip}
                        >
                          {ph.payout.toFixed(0)}
                        </td>
                      ))}
                      <td
                        className="tipcell"
                        onMouseEnter={showTip(y.summary)}
                        onMouseMove={moveTip}
                        onMouseLeave={hideTip}
                      >
                        <strong>{y.total_payout.toFixed(0)}</strong>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="hint-note">Hover any figure for a plain-words explanation.</p>

              <div className="explainer">
                <h3>In plain words</h3>
                <p className="bc-explain">{pricing.burning_cost_explanation}</p>
                <ul>
                  {pricing.years.map((y) => <li key={y.year}>{y.summary}</li>)}
                </ul>
              </div>

              {econ && (
                <div className="pricing-panel">
                  <h3>Pricing this zone</h3>

                  <div className="el-row">
                    <div className="el-box">
                      <span className="el-label">Burning cost</span>
                      <span className="el-val">{econ.economics.burning_cost.toLocaleString()}</span>
                    </div>
                    <div className="el-box">
                      <span className="el-label">Modelled EL</span>
                      <span className="el-val">{econ.economics.modelled_el.toLocaleString()}</span>
                    </div>
                    <div className="el-box chosen">
                      <span className="el-label">Expected loss</span>
                      <span className="el-val">{econ.economics.technical_el.toLocaleString()}</span>
                    </div>
                    <label className="dist-select">
                      Fit
                      <select value={distribution} onChange={(e) => { setDistribution(e.target.value); priceEconomics(openDraft.id, zone, phases, e.target.value) }}>
                        <option value="gamma">gamma</option>
                        <option value="lognormal">lognormal</option>
                        <option value="normal">normal</option>
                      </select>
                    </label>
                  </div>
                  <p className="bc-explain">{econ.explanations.expected_loss}</p>

                  {econ.economics.qq.length > 0 && <QQPlot qq={econ.economics.qq} />}

                  <h4>Loadings</h4>
                  <table className="stages">
                    <thead><tr><th>Name</th><th>Basis</th><th>Value</th><th>Adds</th><th></th></tr></thead>
                    <tbody>
                      {loadings.map((l, i) => (
                        <tr key={i}>
                          <td><input value={l.name} onChange={(e) => editLoading(i, 'name', e.target.value)} /></td>
                          <td>
                            <select value={l.basis} onChange={(e) => editLoading(i, 'basis', e.target.value)}>
                              <option value="pct_el">% of expected loss</option>
                              <option value="pct_gross">% of premium</option>
                              <option value="flat">flat amount</option>
                            </select>
                          </td>
                          <td><input type="number" value={l.value} onChange={(e) => editLoading(i, 'value', e.target.value)} /></td>
                          <td className="normal">
                            {econ.price.loading_breakdown[i]?.amount?.toLocaleString() ?? '—'}
                          </td>
                          <td><button className="del" onClick={() => removeLoading(i)}>✕</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="loading-actions">
                    <button className="secondary" onClick={addLoading}>+ Add loading</button>
                    <button onClick={() => priceEconomics(openDraft.id, zone, phases)} disabled={computing}>
                      {computing ? 'Computing…' : 'Apply'}
                    </button>
                  </div>

                  <div className="premium-box">
                    <div>
                      <span className="premium-rate">{econ.price.premium_rate}%</span>
                      <span className="premium-sub">of sum insured</span>
                    </div>
                    <div>
                      <span className="premium-amt">{econ.price.gross_premium.toLocaleString()}</span>
                      <span className="premium-sub">premium on {econ.sum_insured.toLocaleString()}</span>
                    </div>
                    <span className={`flag ${econ.quality_flag}`} title={`${econ.economics.n_years} years of data`} />
                  </div>
                  <p className="bc-explain">{econ.explanations.premium}</p>
                  <ul className="explainer">
                    {econ.explanations.loadings.map((t, i) => <li key={i}>{t}</li>)}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {tip && (
        <div
          className="hover-tip"
          style={{ left: Math.min(tip.x + 14, window.innerWidth - 340), top: tip.y + 16 }}
        >
          {tip.text}
        </div>
      )}
    </div>
  )
}
