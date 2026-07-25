import { useEffect, useState } from 'react'

import { API } from './config.js'

export default function ZoningPanel({ country, onZones }) {
  const [startYear, setStartYear] = useState(2021)
  const [endYear, setEndYear] = useState(2025)
  const [clusters, setClusters] = useState(15)
  const [sensitivity, setSensitivity] = useState(1.25)
  const [adminSnap, setAdminSnap] = useState(false)
  const [job, setJob] = useState(null)
  const [runs, setRuns] = useState([])
  const [selectedRun, setSelectedRun] = useState(null)
  const [error, setError] = useState(null)
  const [maps, setMaps] = useState([])
  const [approveName, setApproveName] = useState('')
  const [approveMsg, setApproveMsg] = useState(null)

  const loadRuns = () =>
    fetch(`${API}/zoning/runs?country=${country}`)
      .then((r) => r.json())
      .then(setRuns)
      .catch(() => {})

  const loadMaps = () =>
    fetch(`${API}/zone-maps?country=${country}`)
      .then((r) => r.json())
      .then(setMaps)
      .catch(() => {})

  useEffect(() => {
    loadRuns()
    loadMaps()
    setSelectedRun(null)
    onZones(null)
  }, [country])

  useEffect(() => {
    if (!job?.id || job.state === 'SUCCESS' || job.state === 'FAILURE') return
    const t = setInterval(() => {
      fetch(`${API}/jobs/${job.id}`)
        .then((r) => r.json())
        .then((s) => {
          setJob({ id: job.id, state: s.state, progress: s.progress, result: s.result })
          if (s.state === 'SUCCESS') {
            loadRuns()
            if (s.result?.run_id) showRun(s.result.run_id)
          }
        })
        .catch(() => {})
    }, 1500)
    return () => clearInterval(t)
  }, [job?.id, job?.state])

  const startRun = () => {
    setError(null)
    fetch(`${API}/zoning/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        country,
        start_year: +startYear,
        end_year: +endYear,
        n_clusters: +clusters,
        sensitivity: +sensitivity,
        admin_snap: adminSnap,
      }),
    })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json()).detail)
        return r.json()
      })
      .then((d) => setJob({ id: d.job_id, state: 'PENDING' }))
      .catch((e) => setError(e.message))
  }

  const showRun = (runId) => {
    setSelectedRun(runId)
    fetch(`${API}/zoning/runs/${country}/${runId}/geojson`)
      .then((r) => r.json())
      .then(onZones)
      .catch(() => {})
  }

  const approveRun = () => {
    setApproveMsg(null)
    fetch(`${API}/zoning/runs/${country}/${selectedRun}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: approveName, approved_by: 'admin' }),
    })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json()).detail)
        return r.json()
      })
      .then((d) => {
        setApproveMsg(`Approved as ${d.name}`)
        setApproveName('')
        loadMaps()
      })
      .catch((e) => setApproveMsg(e.message))
  }

  const showMap = (name) => {
    setSelectedRun(null)
    fetch(`${API}/zone-maps/${name}/geojson`)
      .then((r) => r.json())
      .then(onZones)
      .catch(() => {})
  }

  const running = job && job.state !== 'SUCCESS' && job.state !== 'FAILURE'

  return (
    <div className="panel">
      <h2>Zoning</h2>
      <label>
        From year
        <input type="number" value={startYear} onChange={(e) => setStartYear(e.target.value)} />
      </label>
      <label>
        To year
        <input type="number" value={endYear} onChange={(e) => setEndYear(e.target.value)} />
      </label>
      <label>
        Number of zones
        <input type="number" min="2" max="50" value={clusters} onChange={(e) => setClusters(e.target.value)} />
      </label>
      <label>
        Rainfall sensitivity
        <input type="number" step="0.25" min="0.5" max="3" value={sensitivity} onChange={(e) => setSensitivity(e.target.value)} />
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={adminSnap}
          onChange={(e) => setAdminSnap(e.target.checked)}
        />{' '}
        Align zones to districts (admin level 2)
      </label>
      <button onClick={startRun} disabled={running}>
        {running ? `Running… ${job.progress?.stage ?? ''}` : 'Run zoning'}
      </button>
      {job?.state === 'FAILURE' && <div className="error">Run failed — check worker logs.</div>}
      {error && <div className="error">{error}</div>}

      {selectedRun && (
        <div className="cached">
          <h3>Approve selected run</h3>
          <label>
            Version name
            <input
              placeholder={`${country}-v${maps.length + 1}`}
              value={approveName}
              onChange={(e) => setApproveName(e.target.value)}
            />
          </label>
          <button
            onClick={approveRun}
            disabled={!approveName.trim()}
          >
            Approve &amp; freeze
          </button>
          {approveMsg && <div className="done">{approveMsg}</div>}
        </div>
      )}

      {maps.length > 0 && (
        <div className="cached">
          <h3>Approved versions</h3>
          {maps.map((m) => (
            <div key={m.name} className="run" onClick={() => showMap(m.name)}>
              <strong>{m.name}</strong>
              <br />
              approved by {m.approved_by} · {m.params.years.length} yrs · {m.params.n_clusters} zones
            </div>
          ))}
        </div>
      )}

      {runs.length > 0 && (
        <div className="cached">
          <h3>Draft runs</h3>
          {runs.map((r) => (
            <div
              key={r.run_id}
              className={`run ${selectedRun === r.run_id ? 'selected' : ''}`}
              onClick={() => showRun(r.run_id)}
            >
              <strong>{r.run_id}</strong>
              {r.quality_flag && <span className={`flag ${r.quality_flag}`} />}
              <br />
              {r.params.years.length} yrs · {r.params.n_clusters} zones · sens {r.params.sensitivity}
              {r.params.admin_snap ? ' · districts' : ''}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
