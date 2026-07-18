"""Launch L5: the admin surface — award / claw back credits + launch metrics.

Admin = an email on SKEPTIC_ADMIN_EMAILS (env). There is no self-serve path to
becoming one; a non-admin gets a 404 (existence hidden), an anon a 401.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app import db
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


def _mark_verified(email: str) -> None:
    with db.session() as s:
        u = s.query(db.User).filter(db.User.email == email.lower()).one()
        u.verified_at = datetime.now(UTC)
        s.commit()


@pytest.fixture()
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A signed-in, VERIFIED admin. Each test gets a unique admin email placed
    on the allowlist (the session DB is shared, so a fixed email would 409 on
    reuse). Whitespace + a second entry prove the allowlist is parsed, not
    matched raw. Verified because admin power is bound to proven mailbox
    control (an allowlisted-but-unverified email is NOT an admin)."""
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")  # dev gate open
    email = f"owner-{uuid.uuid4().hex[:8]}@skeptic.fyi"
    monkeypatch.setenv("SKEPTIC_ADMIN_EMAILS", f" {email} , second-admin@x.com ")
    c = _client()
    _signup(c, email)
    _mark_verified(email)
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


def test_admin_auth_precedes_body_validation(admin_client: TestClient) -> None:
    """require_admin is a Depends → it resolves BEFORE the body is parsed, so an
    unauthenticated caller gets 401, never a 422 that would echo the schema."""
    anon = _client()  # no signup
    r = anon.post(
        "/api/admin/grant-credits", json={"email": "x@x.com", "credits": 9_999_999}
    )
    assert r.status_code == 401  # auth first, not a schema-leaking 422


def test_metrics_shape(admin_client: TestClient) -> None:
    m = admin_client.get("/api/admin/metrics").json()
    assert m["accounts"]["total"] >= 1 and "verified" in m["accounts"]
    assert "by_status" in m["runs"] and "signed_in" in m["runs"]
    assert "outstanding" in m["credits"] and "spent" in m["credits"]
    rev = m["revenue"]
    assert rev["gross_usd"] == rev["purchases"] * 10
    assert rev["net_usd"] == (rev["purchases"] - rev["chargebacks"]) * 10
    assert "today" in m["anon_trials"]


def test_revenue_net_usd_subtracts_chargebacks(admin_client: TestClient) -> None:
    """net_usd nets out reversals: a Stripe refund/dispute (recorded as a
    'chargeback' row) drops net revenue by $10 while gross stays put."""
    before = admin_client.get("/api/admin/metrics").json()["revenue"]
    with db.session() as s:
        uid = s.query(db.User.id).first()[0]
        s.add(db.CreditLedger(user_id=uid, delta=50, reason="purchase", ext_ref="evt_rev1"))
        s.add(db.CreditLedger(user_id=uid, delta=-50, reason="chargeback", ext_ref="pi_rev1"))
        s.commit()
    after = admin_client.get("/api/admin/metrics").json()["revenue"]
    assert after["purchases"] == before["purchases"] + 1
    assert after["chargebacks"] == before["chargebacks"] + 1
    assert after["gross_usd"] == before["gross_usd"] + 10  # gross counts the sale
    assert after["net_usd"] == before["net_usd"]  # ...but the reversal cancels it net


def test_unverified_allowlisted_email_is_not_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The squat defense: an allowlisted email that signed up but is NOT
    verified is not an admin — so an attacker can't claim an allowlisted-but-
    unregistered email and get instant admin. Verifying flips it on."""
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    email = f"squatter-{uuid.uuid4().hex[:8]}@skeptic.fyi"
    monkeypatch.setenv("SKEPTIC_ADMIN_EMAILS", email)
    c = _client()
    _signup(c, email)  # signed up, NOT verified
    assert c.get("/api/me").json()["admin"] is False
    assert c.get("/api/admin/metrics").status_code == 404

    _mark_verified(email)  # proven mailbox control → admin turns on
    assert c.get("/api/me").json()["admin"] is True
    assert c.get("/api/admin/metrics").status_code == 200


def test_no_admin_when_allowlist_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail CLOSED: with no SKEPTIC_ADMIN_EMAILS, nobody is an admin — even an
    email that WOULD be one under a configured allowlist."""
    monkeypatch.setenv("SKEPTIC_ACCESS_TOKEN", "")
    monkeypatch.delenv("SKEPTIC_ADMIN_EMAILS", raising=False)
    c = _client()
    _signup(c, f"owner-{uuid.uuid4().hex[:8]}@skeptic.fyi")
    assert c.get("/api/me").json()["admin"] is False
    assert c.get("/api/admin/metrics").status_code == 404
