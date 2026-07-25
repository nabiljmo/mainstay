import { useEffect, useState } from 'react'

import { API } from './config.js'

export default function WeatherPanel({ country, onCountry }) {
  const [countries, setCountries] = useState([])
  const [startYear, setStartYear] = useState(2021)
  const [endYear, setEndYear] = useState(2025)
  const [job, setJob] = useState(null) // {id, state, progress}
  const [error, setError] = useState(null)

  const loadCountries = () =>
    fetch(`${API}/weather/countries`)
      .then((r) => r.json())
      .then(setCountries)
      .catch(() => {})

  useEffect(() => {
    loadCountries()
  }, [])

  // Poll a running fetch job.
  useEffect(() => {
    if (!job?.id || job.state === 'SUCCESS' || job.state === 'FAILURE') return
    const t = setInterval(() => {
      fetch(`${API}/jobs/${job.id}`)
        .then((r) => r.json())
        .then((s) => {
          setJob({ id: job.id, state: s.state, progress: s.progress })
          if (s.state === 'SUCCESS') loadCountries()
        })
        .catch(() => {})
    }, 1500)
    return () => clearInterval(t)
  }, [job?.id, job?.state])

  const startFetch = () => {
    setError(null)
    fetch(`${API}/weather/fetch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ country, start_year: +startYear, end_year: +endYear }),
    })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json()).detail)
        return r.json()
      })
      .then((d) => setJob({ id: d.job_id, state: 'PENDING' }))
      .catch((e) => setError(e.message))
  }

  const selected = countries.find((c) => c.code === country)
  const yearsChosen = endYear - startYear + 1
  const quality = yearsChosen >= 15 ? 'green' : yearsChosen >= 10 ? 'amber' : 'red'
  const running = job && job.state !== 'SUCCESS' && job.state !== 'FAILURE'
  const pct =
    job?.progress?.days_total
      ? Math.round(
          (100 * (job.progress.years_done * 365 + job.progress.day)) /
            (job.progress.years_total * 365),
        )
      : 0

  return (
    <div className="panel">
      <h2>Weather data</h2>
      <label>
        Country
        <select value={country} onChange={(e) => onCountry(e.target.value)}>
          {countries.map((c) => (
            <option key={c.code} value={c.code}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        From year
        <input type="number" min="1981" value={startYear} onChange={(e) => setStartYear(e.target.value)} />
      </label>
      <label>
        To year
        <input type="number" max="2026" value={endYear} onChange={(e) => setEndYear(e.target.value)} />
      </label>
      <div className={`quality ${quality}`}>
        {yearsChosen} years selected —{' '}
        {quality === 'green'
          ? 'good depth'
          : quality === 'amber'
            ? 'usable; more years recommended'
            : 'short history: tail risk may be underestimated'}
      </div>
      <button onClick={startFetch} disabled={running}>
        {running ? 'Fetching…' : 'Fetch CHIRPS data'}
      </button>
      {running && (
        <div className="progress">
          <div className="bar" style={{ width: `${pct}%` }} />
          <span>
            {job.progress
              ? `${job.progress.year}: day ${job.progress.day}/${job.progress.days_total} (${pct}%)`
              : 'starting…'}
          </span>
        </div>
      )}
      {job?.state === 'SUCCESS' && <div className="done">Fetch complete.</div>}
      {error && <div className="error">{error}</div>}
      <div className="cached">
        <h3>Cached years</h3>
        {selected?.cached_years?.length ? (
          <p>
            {selected.name}: {selected.cached_years.join(', ')}
          </p>
        ) : (
          <p>Nothing cached for {selected?.name ?? 'this country'} yet.</p>
        )}
      </div>
    </div>
  )
}
