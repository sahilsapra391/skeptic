"""Account-shared bits (launch L1/L1b).

The runs DB deliberately falls back to a throwaway local SQLite when the
configured database is unreachable (db.init_db). Accounts and credits must
NOT inherit that: a ledger written to a container-local file evaporates on
redeploy. When the fallback is active, user auth refuses loudly (503)
while charts and the parser stay up.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("auth.accounts")

GRANT_REASON = "signup_grant"


class AccountsUnavailableError(Exception):
    """The accounts database is unreachable — refuse rather than write
    grants/debits/sessions to the throwaway SQLite fallback."""


def signup_grant_credits() -> int:
    raw = os.environ.get("SIGNUP_GRANT_CREDITS", "5")
    try:
        return max(0, int(raw))
    except ValueError:
        log.error("SIGNUP_GRANT_CREDITS=%r is not an integer — using 5", raw)
        return 5
