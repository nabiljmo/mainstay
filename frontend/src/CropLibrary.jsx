import { useEffect, useState } from 'react'

import { API } from './config.js'

export default function CropLibrary() {
  const [crops, setCrops] = useState([])
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
  }, [])

  const select = (c) => {
    setSelected(c)
    setDraft(JSON.parse(JSON.stringify({ stages: c.stages, seasons: c.seasons, source: c.source })))
    setWarnings([])
    setMsg(null)
  }

  const editStage = (i, field, value) => {
    const d = { ...draft }
    d.stages[i][field] = field === 'name' ? value : Number(value)
    setDraft(d)
  }

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
        load()
      })
      .catch((e) => setMsg(String(e)))
  }

  const sensSum = draft ? draft.stages.reduce((a, s) => a + (s.sensitivity || 0), 0) : 0

  return (
    <div className="crop-view">
      <div className="crop-list">
        <h2>Crops</h2>
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
              </tr>
            </thead>
            <tbody>
              {draft.seasons.map((s, i) => (
                <tr key={i}>
                  <td>{s.country}</td>
                  <td>{s.season}</td>
                  <td>{s.plant_start}</td>
                  <td>{s.plant_end}</td>
                </tr>
              ))}
            </tbody>
          </table>

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
