"""Authentication and role-based access.

Five roles — admin, actuary, agronomist, agent, operations — gate who can do
what. Auth is a plain opaque session token in an httpOnly, SameSite=Lax cookie:
it rides both the SPA's fetches and direct browser navigations (assumption
sheet, agent page) without extra plumbing, and the server can expire or revoke
it at will. Passwords are salted PBKDF2-HMAC-SHA256 (stdlib — no new deps).

admin is a superuser: it passes every role check. Every other role sees only
its own surface, enforced here on the server (the UI hiding a tab is a
convenience, not the boundary).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException

from app.db import connect

ROLES = ("admin", "actuary", "agronomist", "agent", "operations")
COOKIE_NAME = "aez_session"
SESSION_HOURS = 12
_ITERATIONS = 200_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_by    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
"""


# ----- password hashing (salted PBKDF2, stdlib) -----

def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iters, salt, digest = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


# ----- schema + seed -----

def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)


def seed_admin() -> None:
    """Create the first admin if there are no users, so the platform is usable.
    Username/password come from env (AEZ_ADMIN_USER / AEZ_ADMIN_PASSWORD) with
    dev defaults — change the password on first login."""
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if n:
        return
    username = os.environ.get("AEZ_ADMIN_USER", "admin")
    password = os.environ.get("AEZ_ADMIN_PASSWORD", "changeme")
    create_user(username, password, "admin", created_by="seed")


# ----- user management -----

def create_user(username: str, password: str, role: str, created_by: str = "system") -> dict:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; must be one of {', '.join(ROLES)}")
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE username = %s", (username,)).fetchone()
        if exists:
            raise ValueError(f"user {username!r} already exists")
        row = conn.execute(
            """INSERT INTO users (username, password_hash, role, created_by)
               VALUES (%s,%s,%s,%s) RETURNING id, username, role, active, created_at""",
            (username, hash_password(password), role, created_by),
        ).fetchone()
    return {"id": row[0], "username": row[1], "role": row[2], "active": row[3],
            "created_at": row[4].isoformat()}


def set_active(username: str, active: bool) -> None:
    if not active and _admin_count(exclude=username) == 0 and _role_of(username) == "admin":
        raise ValueError("cannot deactivate the last active admin")
    with connect() as conn:
        conn.execute("UPDATE users SET active = %s WHERE username = %s", (active, username))
        if not active:
            conn.execute(
                "DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = %s)",
                (username,))


def set_role(username: str, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    if role != "admin" and _role_of(username) == "admin" and _admin_count(exclude=username) == 0:
        raise ValueError("cannot remove the last admin's admin role")
    with connect() as conn:
        conn.execute("UPDATE users SET role = %s WHERE username = %s", (role, username))


def set_password(username: str, password: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE users SET password_hash = %s WHERE username = %s",
                     (hash_password(password), username))


def list_users() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT username, role, active, created_by, created_at FROM users ORDER BY username"
        ).fetchall()
    return [{"username": r[0], "role": r[1], "active": r[2], "created_by": r[3],
             "created_at": r[4].isoformat()} for r in rows]


def _role_of(username: str) -> str | None:
    with connect() as conn:
        r = conn.execute("SELECT role FROM users WHERE username = %s", (username,)).fetchone()
    return r[0] if r else None


def _admin_count(exclude: str | None = None) -> int:
    with connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE role='admin' AND active AND username <> %s",
            (exclude or "",)).fetchone()[0]


# ----- login / sessions -----

def login(username: str, password: str) -> str | None:
    """Validate credentials; on success create a session and return its token."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id, password_hash, active FROM users WHERE username = %s", (username,)
        ).fetchone()
    if not row or not row[2] or not verify_password(password, row[1]):
        return None
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    with connect() as conn:
        conn.execute("INSERT INTO sessions (token, user_id, expires_at) VALUES (%s,%s,%s)",
                     (token, row[0], expires))
    return token


def logout(token: str | None) -> None:
    if not token:
        return
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = %s", (token,))


def user_for_token(token: str | None) -> dict | None:
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            """SELECT u.username, u.role, u.active, s.expires_at
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token = %s""",
            (token,),
        ).fetchone()
    if not row:
        return None
    username, role, active, expires_at = row
    if not active or expires_at <= datetime.now(timezone.utc):
        return None
    return {"username": username, "role": role}


# ----- FastAPI dependencies -----

def current_user(aez_session: str | None = Cookie(default=None)) -> dict:
    user = user_for_token(aez_session)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def require(*roles: str):
    """Dependency: allow only the given roles (admin always allowed). No roles
    means 'any authenticated user'."""
    allowed = set(roles)

    def _dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] != "admin" and allowed and user["role"] not in allowed:
            raise HTTPException(403, f"Requires role: {', '.join(sorted(allowed))}")
        return user

    return _dep
