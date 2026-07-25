---
id: 013
title: Roles and access control
type: AFK
labels: [needs-triage]
blocked_by: [001]
user_stories: [33]
phase: 3
---

## What to build

Authentication and role-based access: admin, actuary, agronomist, agent, operations. Login; role-gated screens and API routes (agents cannot see the workbench; only actuaries publish; only operations release payout files). Agent data scoping — an agent sees only quotes and policies they created. Admin manages users.

## Acceptance criteria

- [x] Login with per-user accounts; sessions expire sensibly
- [x] Each role sees only its screens; API enforces the same server-side
- [x] Agent scoping verified: agent A cannot read agent B's quotes/policies
- [x] Admin can create/deactivate users and assign roles
- [x] Tests: route-level authorisation matrix

## Blocked by

- 001-walking-skeleton

## Notes

- `app/auth.py`: users + sessions tables, salted PBKDF2 hashing (stdlib, no new
  deps), opaque session token in an httpOnly SameSite=Lax cookie (rides SPA
  fetches and direct HTML navigations alike), 12-hour expiry, `current_user` +
  `require(*roles)` dependencies. admin is a superuser. Seeds a first admin
  (AEZ_ADMIN_USER / AEZ_ADMIN_PASSWORD, default admin / changeme — change it).
- Every mutating/sensitive route is role-gated server-side, and identity
  (approved_by / edited_by / created_by / published_by) is now taken from the
  session, not a spoofable request field. Quotes are scoped to the creating
  agent: agent A gets 403 on agent B's quote; operations/admin may read any.
- Admin user management: `GET/POST /admin/users`, `PATCH /admin/users/{u}`
  (role, active, password), with guards against locking out the last admin.
- Frontend: login screen, credentialed fetch (one shim in main.jsx), role-gated
  tabs, admin Users screen, an Operations demand-signals view, a user chip +
  sign-out. The agent page gained its own login gate.
- `tests/test_auth.py`: login/session/expiry, the role×route matrix, agent
  cross-read denial, and admin user CRUD (97 tests pass overall).

Deferred: policies don't exist yet (issue 015) — quote scoping is in place and
will extend to policies when they land. Payout-file release stays operations-
only when 017 is built. Cookies are not `Secure` (localhost http); set that in
production behind TLS.
