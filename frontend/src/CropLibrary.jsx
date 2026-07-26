import { useEffect, useState } from 'react'

import { API } from './config.js'

export default function CropLibrary() {
  const [crops, setCrops] = useState([])
  const [countries, setCountries] = useState([])
  const [selected, setSelected] = useState(null)
  const [draft, setDraft] = useState(null) // editable copy
  const [warnings, setWarnings] = useState([])
  const [msg, setMsg] = useState(null)

  const load = () =>
    fetch(`${API}/crops`)
      .then((r) => r.json())
      .then((cs) => {
        setCrops(cs)
        if (cs.length && !selected) select(cs[0])
      })
      .catch(() => {})

  useEffect(() => {
    load()
    fetch(`${API}/weather/countries`).then((r) => r.json()).then(setCountries).catch(() => {})
  }, [])

  const select = (c) => {
    setSelected(c)
    setDraft(JSON.parse(JSON.stringify({ stages: c.stages, seasons: c.seasons, source: c.source })))
    setWarnings([])
    setMsg(null)
  }

  // Start a brand-new crop. Saving it writes version 1 (the POST creates the
  // crop the first time a version is written). Stages seed from the FAO maize
  // template so the agronomist edits rather than starts blank.
  const newCrop = () => {
    const name = window.prompt('New crop name (e.g. sorghum)')?.trim().toLowerCase()
    if (!name) return
    if (crops.some((c) => c.crop === name)) {
      const existing = crops.find((c) => c.crop === name)
      setMsg(`Crop "${name}" already exists — opened it to edit.`)
      select(existing)
      return
    }
    setSelected({ crop: name, version: 0, reviewed: false, source: '' })
    setDraft({
      stages: [
        { name: 'establishment', days: 20, sensitivity: 0.15 },
        { name: 'vegetative', days: 35, sensitivity: 0.20 },
        { name: 'flowering', days: 25, sensitivity: 0.40 },
        { name: 'grain_filling', days: 40, sensitivity: 0.25 },
      ],
      seasons: [],
      source: '',
    })
    setWarnings([])
    setMsg(null)
  }

  const editStage = (i, field, value) => {
    const d = { ...draft }
    d.stages[i][field] = field === 'name' ? value : Number(value)
    setDraft(d)
  }

  const editSeason = (i, field, value) => {
    const d = { ...draft }
    d.seasons = d.seasons.map((s, j) => (j === i ? { ...s, [field]: value } : s))
    setDraft(d)
  }
  const addSeason = () => {
    const country = countries[0]?.code ?? 'KEN'
    setDraft({
      ...draft,
      seasons: [...draft.seasons, { country, season: 'long_rains', plant_start: '03-15', plant_end: '04-15' }],
    })
  }
  const removeSeason = (i) => setDraft({ ...draft, seasons: draft.seasons.filter((_, j) => j !== i) })

  const save = () => {
    setMsg(null)
    fetch(`${API}/crops/${selected.crop}/versions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        stages: draft.stages,
        seasons: draft.seasons,
        source: draft.source,
        edited_by: 'admin',
        reviewed: true,
      }),
    })
      .then((r) => r.json())
      .then((rec) => {
        setWarnings(rec.warnings || [])
        setMsg(`Saved version ${rec.version}`)
        setSelected(rec) // a new crop is now a real, editable version
        load()
      })
      .catch((e) => setMsg(String(e)))
  }

  const sensSum = draft ? draft.stages.reduce((a, s) => a + (s.sensitivity || 0), 0) : 0

  return (
    <div className="crop-view">
      <div className="crop-list">
        <h2>Crops</h2>
        <button className="secondary" onClick={newCrop} style={{ marginBottom: '0.5rem' }}>+ New crop</button>
        {selected && selected.version === 0 && (
          <div className="run selected"><strong>{selected.crop}</strong> · new</div>
        )}
        {crops.map((c) => (
          <div
            key={c.crop}
            className={`run ${selected?.crop === c.crop ? 'selected' : ''}`}
            onClick={() => select(c)}
          >
            <strong>{c.crop}</strong> v{c.version}
            {!c.reviewed && <span className="unreviewed"> · unreviewed</span>}
            <br />
            {c.source ? 'FAO-seeded' : 'custom'}
          </div>
        ))}
      </div>

      {draft && selected && (
        <div className="crop-edit">
          <h2>
            {selected.crop} <small>(editing creates v{selected.version + 1})</small>
          </h2>

          <h3>Growth stages</h3>
          <table className="stages">
            <thead>
              <tr>
                <th>Stage</th>
                <th>Days</th>
                <th>Water-stress sensitivity</th>
              </tr>
            </thead>
            <tbody>
              {draft.stages.map((s, i) => (
                <tr key={i}>
                  <td>
                    <input value={s.name} onChange={(e) => editStage(i, 'name', e.target.value)} />
                  </td>
                  <td>
                    <input
                      type="number"
                      value={s.days}
                      onChange={(e) => editStage(i, 'days', e.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      step="0.05"
                      value={s.sensitivity}
                      onChange={(e) => editStage(i, 'sensitivity', e.target.value)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className={`sens-sum ${Math.abs(sensSum - 1) < 1e-6 ? 'ok' : 'warn'}`}>
            sensitivity sums to {sensSum.toFixed(2)} (should be 1.00)
          </div>

          <h3>Planting windows</h3>
          <table className="stages">
            <thead>
              <tr>
                <th>Country</th>
                <th>Season</th>
                <th>Plant start</th>
                <th>Plant end</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {draft.seasons.map((s, i) => (
                <tr key={i}>
                  <td>
                    <select value={s.country} onChange={(e) => editSeason(i, 'country', e.target.value)}>
                      {countries.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
                    </select>
                  </td>
                  <td>
                    <select value={s.season} onChange={(e) => editSeason(i, 'season', e.target.value)}>
                      <option value="long_rains">long_rains</option>
                      <option value="short_rains">short_rains</option>
                    </select>
                  </td>
                  <td>
                    <input value={s.plant_start} placeholder="MM-DD"
                      onChange={(e) => editSeason(i, 'plant_start', e.target.value)} />
                  </td>
                  <td>
                    <input value={s.plant_end} placeholder="MM-DD"
                      onChange={(e) => editSeason(i, 'plant_end', e.target.value)} />
                  </td>
                  <td><button className="del" onClick={() => removeSeason(i)}>✕</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <button className="secondary" onClick={addSeason}>+ Add planting window</button>

          <button onClick={save}>Save as new version</button>
          {msg && <div className="done">{msg}</div>}
          {warnings.map((w, i) => (
            <div key={i} className="error">
              {w}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
