"""Shared test helpers. The `login` fixture creates a throwaway user of a given
role and signs the TestClient in as them (the session cookie then rides every
later request on that client). Throwaway users are tagged created_by='test' so
the DB fixtures can sweep them up in teardown."""

import uuid

import pytest


@pytest.fixture
def login():
    def _login(client, role: str, username: str | None = None, password: str = "pw"):
        from app import auth

        username = username or f"t_{role}_{uuid.uuid4().hex[:8]}"
        try:
            auth.create_user(username, password, role, created_by="test")
        except ValueError:
            pass  # already exists — reuse it
        r = client.post("/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return username

    return _login
