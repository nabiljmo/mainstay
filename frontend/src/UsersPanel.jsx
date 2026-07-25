import { useEffect, useState } from 'react'

const API = 'http://localhost:8000'
const ROLES = ['admin', 'actuary', 'agronomist', 'agent', 'operations']

export default function UsersPanel() {
  const [users, setUsers] = useState([])
  const [form, setForm] = useState({ username: '', password: '', role: 'agent' })
  const [error, setError] = useState(null)
  const [msg, setMsg] = useState(null)

  const load = () => fetch(`${API}/admin/users`).then((r) => r.json()).then(setUsers).catch(() => {})
  useEffect(() => { load() }, [])

  const create = (e) => {
    e.preventDefault()
    setError(null); setMsg(null)
    fetch(`${API}/admin/users`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form),
    })
      .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); return r.json() })
      .then((u) => { setMsg(`Created ${u.username}`); setForm({ username: '', password: '', role: 'agent' }); load() })
      .catch((err) => setError(err.message))
  }

  const patch = (username, body) => {
    setError(null); setMsg(null)
    fetch(`${API}/admin/users/${username}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
      .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); return r.json() })
      .then(() => load())
      .catch((err) => setError(err.message))
  }

  return (
    <div className="crop-view">
      <div className="crop-list">
        <h2>New user</h2>
        <form onSubmit={create}>
          <label>Username
            <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          </label>
          <label>Password
            <input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </label>
          <label>Role
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <button type="submit" disabled={!form.username || !form.password}>Create user</button>
        </form>
        {msg && <div className="done">{msg}</div>}
        {error && <div className="error">{error}</div>}
      </div>

      <div className="crop-edit">
        <h2>Users</h2>
        <table className="stages" style={{ maxWidth: 640 }}>
          <thead><tr><th>Username</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.username}>
                <td><strong>{u.username}</strong></td>
                <td>
                  <select value={u.role} onChange={(e) => patch(u.username, { role: e.target.value })}>
                    {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                </td>
                <td>{u.active ? <span className="badge-on">active</span> : <span className="badge-off">disabled</span>}</td>
                <td>
                  <button className="secondary" onClick={() => patch(u.username, { active: !u.active })}>
                    {u.active ? 'Deactivate' : 'Reactivate'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="hint-note">Roles gate both the screens here and the API itself. Admin is a superuser.</p>
      </div>
    </div>
  )
}
