"""Owner authentication for Ascent: single-owner passphrase + signed session cookie.

Model: one owner per instance. On first run the owner creates a passphrase
(setup); it is stored as a salted PBKDF2 hash. Login issues a signed,
expiring session token in an HttpOnly cookie. Every /api route except
/api/auth/* (and /api/status) requires a valid session.

Setup can be claimed only from localhost, or from anywhere with the one-time
bootstrap token (env ASCENT_BOOTSTRAP_TOKEN) sent as the X-Ascent-Bootstrap
header - so an arbitrary internet visitor cannot claim a fresh deployment
before the owner reaches it. Setup is permanently disabled once an owner
exists. Logout bumps the session epoch, invalidating every issued token.

CSRF: mutating /api requests must carry the X-Ascent-Client: 1 header. A
cross-site form/fetch cannot set it without a CORS preflight this app never
answers, and the SameSite=Lax cookie is not sent cross-site anyway.

Demo mode (ASCENT_DEMO=1) is the PUBLIC fictional deployment: all reads are
open, every mutation is rejected with demo_read_only, the LLM endpoints are
rejected, and the LLM client is force-disabled so a configured key cannot be
spent by public traffic.

No third-party auth dependencies: pbkdf2_hmac + hmac + secrets from the
standard library.
"""

import hashlib
import hmac
import os
import secrets
import threading
import time

from . import db as dbmod

_setup_lock = threading.Lock()

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
CSRF_HEADER = "x-ascent-client"
BOOTSTRAP_HEADER = "x-ascent-bootstrap"

COOKIE_NAME = "ascent_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600
_PBKDF2_ROUNDS = 200_000

K_PASS_HASH = "_pass_hash"
K_PASS_SALT = "_pass_salt"
K_SECRET = "_session_secret"
K_EPOCH = "_session_epoch"


def _epoch(conn) -> int:
    rows = dbmod.query(conn, "SELECT value FROM profile_field WHERE key = ?", (K_EPOCH,))
    return int(rows[0]["value"]) if rows else 0


def bump_epoch(conn) -> None:
    """Invalidate every outstanding session (logout / owner reset)."""
    conn.execute(
        "INSERT INTO profile_field (key, value) VALUES (?, '1') "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1",
        (K_EPOCH,))
    conn.commit()


def setup_allowed(client_host: str | None, bootstrap_header: str | None) -> bool:
    """First-run setup is claimable only from localhost or with the one-time
    bootstrap token - never by an arbitrary internet visitor."""
    if client_host in LOOPBACK_HOSTS:
        return True
    token = os.environ.get("ASCENT_BOOTSTRAP_TOKEN")
    return bool(token) and bool(bootstrap_header) and hmac.compare_digest(bootstrap_header, token)


def demo_mode() -> bool:
    return os.environ.get("ASCENT_DEMO") == "1"


def _secret(conn) -> str:
    rows = dbmod.query(conn, "SELECT value FROM profile_field WHERE key = ?", (K_SECRET,))
    if rows:
        return rows[0]["value"]
    val = secrets.token_hex(32)
    conn.execute("INSERT INTO profile_field (key, value) VALUES (?, ?)", (K_SECRET, val))
    conn.commit()
    return val


def needs_setup(conn) -> bool:
    return not dbmod.query(conn, "SELECT 1 AS x FROM profile_field WHERE key = ?", (K_PASS_HASH,))


def setup_passphrase(conn, passphrase: str) -> None:
    """Create the owner passphrase. Only allowed when none exists; the lock
    plus re-check makes concurrent first-run claims single-winner."""
    with _setup_lock:
        if not needs_setup(conn):
            raise ValueError("passphrase already set")
        if len(passphrase) < 8:
            raise ValueError("passphrase must be at least 8 characters")
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), bytes.fromhex(salt),
                                     _PBKDF2_ROUNDS).hex()
        conn.execute("INSERT INTO profile_field (key, value) VALUES (?, ?)", (K_PASS_SALT, salt))
        conn.execute("INSERT INTO profile_field (key, value) VALUES (?, ?)", (K_PASS_HASH, digest))
        conn.commit()


def verify_passphrase(conn, passphrase: str) -> bool:
    rows = {r["key"]: r["value"] for r in dbmod.query(
        conn, "SELECT key, value FROM profile_field WHERE key IN (?, ?)", (K_PASS_HASH, K_PASS_SALT))}
    if K_PASS_HASH not in rows:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", passphrase.encode(),
                                 bytes.fromhex(rows[K_PASS_SALT]), _PBKDF2_ROUNDS).hex()
    return hmac.compare_digest(digest, rows[K_PASS_HASH])


def issue_token(conn) -> str:
    expiry = int(time.time()) + SESSION_TTL_SECONDS
    epoch = _epoch(conn)
    msg = f"{epoch}.{expiry}"
    sig = hmac.new(_secret(conn).encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}.{sig}"


def valid_token(conn, token: str | None) -> bool:
    if not token or token.count(".") != 2:
        return False
    try:
        epoch_s, expiry_s, sig = token.split(".")
        epoch, expiry = int(epoch_s), int(expiry_s)
    except ValueError:
        return False
    if expiry < time.time() or epoch != _epoch(conn):
        return False
    expected = hmac.new(_secret(conn).encode(), f"{epoch_s}.{expiry_s}".encode(),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)
