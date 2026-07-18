"""Launch L5: the admin surface — award / claw back credits + launch metrics.

Admin = an email on SKEPTIC_ADMIN_EMAILS (env). There is no self-serve path to
becoming one; a non-admin gets a 404 (existence hidden), an anon a 401.
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

PASSWORD = "correct-horse-battery"

# distinct octet from the other suites (credits 10.55 / billing 10.44 / anon 10.88)
_ip_counter = itertools.count(1)


def _fresh_ip() -> dict[str, str]:
    n = next(_ip_counter)
    return {"x-forwarded-for": f"10.33.{n // 250}.{n % 250}"}


def _email() -> str:
    return f"admin-t-{uuid.uuid4().hex[:8]}@example.com"


def _client() -> TestClient:
    return TestClient(app, base_url="https://testserver")


def _signup(client: TestClient, email: str) -> None:
    r = client.post(
        "/api/auth/signup", json={"email": email, "password": PASSWORD}, headers=_fresh_ip()
    )
    assert r.status_code == 200, r.text


def _credits(client: TestClient) -> int:
    return int(client.get("/api/me").json()["credits"])


@pytest.fixture()
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A signed-in ADMIN. Each test gets a unique admin email placed on the
    allowlist (the session DB is shared, so a fixed email would 409 on reuse).
    Whitespace + a second entry prove the allowlist is parsed, not matched raw."""
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")  # dev gate open
    email = f"owner-{uuid.uuid4().hex[:8]}@skeptic.fyi"
    monkeypatch.setenv("SKEPTIC_ADMIN_EMAILS", f" {email} , second-admin@x.com ")
    c = _client()
    _signup(c, email)
    return c


def test_me_reports_the_admin_flag(admin_client: TestClient) -> None:
    assert admin_client.get("/api/me").json()["admin"] is True
    # a different account, NOT on the allowlist, is not an admin
    other = _client()
    _signup(other, _email())
    assert other.get("/api/me").json()["admin"] is False


def test_admin_awards_credits(admin_client: TestClient) -> None:
    target = _client()
    temail = _email()
    _signup(target, temail)
    assert _credits(target) == 5

    r = admin_client.post(
        "/api/admin/grant-credits", json={"email": temail.upper(), "credits": 3000}
    )
    assert r.status_code == 200
    assert r.json() == {"email": temail, "before": 5, "after": 3005, "delta": 3000}
    assert _credits(target) == 3005  # the target sees the award immediately


def test_admin_can_claw_back(admin_client: TestClient) -> None:
    target = _client()
    temail = _email()
    _signup(target, temail)
    r = admin_client.post("/api/admin/grant-credits", json={"email": temail, "credits": -2})
    assert r.status_code == 200 and r.json()["after"] == 3


def test_grant_to_unknown_email_is_404(admin_client: TestClient) -> None:
    r = admin_client.post(
        "/api/admin/grant-credits", json={"email": "ghost@example.com", "credits": 5}
    )
    assert r.status_code == 404


def test_grant_zero_is_rejected(admin_client: TestClient) -> None:
    target = _client()
    temail = _email()
    _signup(target, temail)
    r = admin_client.post("/api/admin/grant-credits", json={"email": temail, "credits": 0})
    assert r.status_code == 422


def test_non_admin_is_404_on_grant_and_metrics(admin_client: TestClient) -> None:
    # admin_client's fixture set the allowlist; this fresh account is NOT on it
    nonadmin = _client()
    _signup(nonadmin, _email())
    assert nonadmin.post(
        "/api/admin/grant-credits", json={"email": "x@x.com", "credits": 5}
    ).status_code == 404  # 404, not 403 — existence is hidden
    assert nonadmin.get("/api/admin/metrics").status_code == 404


def test_anon_is_401_on_admin(admin_client: TestClient) -> None:
    anon = _client()  # no signup
    assert anon.post(
        "/api/admin/grant-credits", json={"email": "x@x.com", "credits": 5}
    ).status_code == 401
    assert anon.get("/api/admin/metrics").status_code == 401


def test_metrics_shape(admin_client: TestClient) -> None:
    m = admin_client.get("/api/admin/metrics").json()
    assert m["accounts"]["total"] >= 1 and "verified" in m["accounts"]
    assert "by_status" in m["runs"] and "signed_in" in m["runs"]
    assert "outstanding" in m["credits"] and "spent" in m["credits"]
    assert m["revenue"]["gross_usd"] == m["revenue"]["purchases"] * 10
    assert "today" in m["anon_trials"]


def test_no_admin_when_allowlist_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail CLOSED: with no SKEPTIC_ADMIN_EMAILS, nobody is an admin — even an
    email that WOULD be one under a configured allowlist."""
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    monkeypatch.delenv("SKEPTIC_ADMIN_EMAILS", raising=False)
    c = _client()
    _signup(c, f"owner-{uuid.uuid4().hex[:8]}@skeptic.fyi")
    assert c.get("/api/me").json()["admin"] is False
    assert c.get("/api/admin/metrics").status_code == 404
