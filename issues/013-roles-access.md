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

- [ ] Login with per-user accounts; sessions expire sensibly
- [ ] Each role sees only its screens; API enforces the same server-side
- [ ] Agent scoping verified: agent A cannot read agent B's quotes/policies
- [ ] Admin can create/deactivate users and assign roles
- [ ] Tests: route-level authorisation matrix

## Blocked by

- 001-walking-skeleton
