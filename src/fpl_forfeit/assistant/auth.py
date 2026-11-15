"""Single-user sessions in signed, expiring cookies. No password or signing key defaults.

A session is a stateless HMAC-signed token, so it survives a backend restart (free Render
hosts restart and sleep often). The signing secret comes only from the environment.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from threading import BoundedSemaphore, Lock
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

ITERATIONS = 600_000
MIN_SECRET_LENGTH = 32
TOKEN_VERSION = "v1"
RENEW_AFTER_SECONDS = 15
MAX_REVOKED = 64


def password_hash(password: str) -> str:
    passcode = len(password) == 8 and password.isascii() and password.isdigit()
    if not passcode and not 14 <= len(password) <= 256:
        raise ValueError("Use an 8-digit passcode or a unique passphrase of 14–256 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def _parse_hash(value: str) -> tuple[int, bytes, bytes]:
    scheme, work, salt, digest = value.split("$")
    iterations = int(work)
    if scheme != "pbkdf2_sha256" or not ITERATIONS <= iterations <= 2_000_000:
        raise ValueError("Unsupported password hash")
    salt_bytes, digest_bytes = bytes.fromhex(salt), bytes.fromhex(digest)
    if len(salt_bytes) != 16 or len(digest_bytes) != 32:
        raise ValueError("Invalid password hash")
    return iterations, salt_bytes, digest_bytes


@dataclass(frozen=True, slots=True)
class AuthSettings:
    origin: str = ""
    encoded_password: str = field(default="", repr=False)
    session_seconds: int = 28_800
    idle_seconds: int = 3_600
    development_http: bool = False
    # Only enable behind a proxy that appends the real peer address to X-Forwarded-For
    # (Render). Without a trusted proxy the header is attacker-controlled and ignored.
    trusted_proxy: bool = False
    # HMAC key for session cookies (FPL_ASSISTANT_SESSION_SECRET). Rotating it signs everyone out.
    session_secret: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls) -> AuthSettings:
        return cls(
            origin=(
                os.getenv("FPL_ASSISTANT_ORIGIN") or os.getenv("RENDER_EXTERNAL_URL", "")
            ).rstrip("/"),
            encoded_password=os.getenv("FPL_ASSISTANT_PASSWORD_HASH", ""),
            development_http=os.getenv("FPL_ASSISTANT_DEV_HTTP", "") == "1",
            trusted_proxy=os.getenv("FPL_TRUSTED_PROXY", "") == "1",
            session_secret=os.getenv("FPL_ASSISTANT_SESSION_SECRET", ""),
        )

    @property
    def enabled(self) -> bool:
        try:
            _parse_hash(self.encoded_password)
            if len(self.session_secret.encode()) < MIN_SECRET_LENGTH:
                return False
            url = urlsplit(self.origin)
            secure = url.scheme == "https"
            local = (
                self.development_http
                and url.scheme == "http"
                and url.hostname in {"localhost", "127.0.0.1", "::1"}
            )
            return bool(
                url.netloc
                and not url.username
                and not url.password
                and not url.path
                and not url.query
                and not url.fragment
                and (secure or local)
                and 60 <= self.idle_seconds <= self.session_seconds <= 28_800
            )
        except (ValueError, TypeError):
            return False

    @property
    def secure(self) -> bool:
        return self.origin.startswith("https://")

    @property
    def cookie_name(self) -> str:
        return "__Host-fpl-assistant" if self.secure else "fpl-assistant-development"


@dataclass(slots=True)
class Session:
    subject: str
    csrf: str = field(repr=False)
    expires_at: float
    last_seen: float


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SessionStore:
    """Stateless signed-cookie sessions: HMAC-SHA256 over subject, absolute expiry and last activity.

    The idle timeout is enforced by re-issuing the cookie on authenticated activity. Logout adds the
    subject to a small in-memory deny list; a restart clears that list, so a cookie copied before
    logout could work again until its absolute expiry (at most eight hours).
    """

    def __init__(self, settings: AuthSettings | None = None, *, clock=time.time) -> None:
        self.settings = settings or AuthSettings.from_environment()
        self.clock = clock
        self.lock = Lock()
        self.hash_slots = BoundedSemaphore(2)
        self.revoked: dict[str, float] = {}
        self.attempts: dict[str, deque[float]] = {}
        self.global_attempts: deque[float] = deque()

    # --- token handling ---------------------------------------------------------------------

    def _mac(self, message: str) -> str:
        key = self.settings.session_secret.encode()
        return _b64(hmac.new(key, message.encode(), hashlib.sha256).digest())

    def _sign(self, payload: str) -> str:
        return self._mac(f"{TOKEN_VERSION}.{payload}")

    def _csrf(self, subject: str) -> str:
        return self._mac(f"csrf.{subject}")

    def _encode(self, session: Session) -> str:
        body = json.dumps(
            {"s": session.subject, "e": session.expires_at, "u": session.last_seen},
            separators=(",", ":"),
        )
        payload = _b64(body.encode())
        return f"{TOKEN_VERSION}.{payload}.{self._sign(payload)}"

    def _decode(self, token: str) -> Session | None:
        try:
            version, payload, signature = token.split(".")
            if version != TOKEN_VERSION or not hmac.compare_digest(
                signature.encode(), self._sign(payload).encode()
            ):
                return None
            values = json.loads(_unb64(payload))
            subject = str(values["s"])
            expires_at, last_seen = float(values["e"]), float(values["u"])
        except (ValueError, KeyError, TypeError, UnicodeError):
            return None
        return Session(subject, self._csrf(subject), expires_at, last_seen)

    def _purge(self, now: float) -> None:
        for subject, expires_at in list(self.revoked.items()):
            if expires_at <= now:
                del self.revoked[subject]

    def check_origin(self, request: Request, *, write: bool = False) -> None:
        origin = request.headers.get("origin")
        site = request.headers.get("sec-fetch-site")
        if site and site not in {"same-origin", "none"}:
            raise HTTPException(403, "Private access requires the assistant origin.")
        if (write and origin != self.settings.origin) or (
            origin and origin != self.settings.origin
        ):
            raise HTTPException(403, "Private access requires the assistant origin.")
        # The public HTTPS origin is fixed in configuration. TLS can terminate at
        # Render's proxy; do not derive a trusted origin from forwarding headers.
        if request.headers.get("host", "") != urlsplit(self.settings.origin).netloc:
            raise HTTPException(403, "Private access requires the configured origin.")

    def client_address(self, request: Request) -> str:
        """Rate-limit key: the real client IP, never a visitor-controlled value.

        Behind one trusted proxy (Render) the *rightmost* X-Forwarded-For entry is the
        address that proxy observed; everything to its left was supplied by the client
        and can be forged, so it is never used.
        """
        peer = request.client.host if request.client else "unknown"
        if not self.settings.trusted_proxy:
            return peer
        entries = ",".join(request.headers.getlist("x-forwarded-for")).split(",")
        candidate = entries[-1].strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            return peer

    def _rate_limit(self, address: str) -> None:
        now = self.clock()
        with self.lock:
            for key in list(self.attempts):
                while self.attempts[key] and self.attempts[key][0] <= now - 600:
                    self.attempts[key].popleft()
                if not self.attempts[key]:
                    del self.attempts[key]
            while self.global_attempts and self.global_attempts[0] <= now - 600:
                self.global_attempts.popleft()
            queue = self.attempts.setdefault(address, deque())
            if len(queue) >= 5 or len(self.global_attempts) >= 20:
                raise HTTPException(
                    429,
                    "Please wait before trying to sign in again.",
                    headers={"Retry-After": "600"},
                )
            if len(self.attempts) > 256:
                del self.attempts[next(iter(self.attempts))]
            queue.append(now)
            self.global_attempts.append(now)

    def login(self, password: str, address: str) -> tuple[str, Session]:
        if not self.settings.enabled:
            raise HTTPException(503, "Private access has not been configured.")
        self._rate_limit(address)
        if not self.hash_slots.acquire(blocking=False):
            raise HTTPException(429, "Please wait before trying to sign in again.")
        try:
            iterations, salt, digest = _parse_hash(self.settings.encoded_password)
            try:
                encoded = password.encode()
            except UnicodeEncodeError:
                encoded = b""
            candidate = hashlib.pbkdf2_hmac("sha256", encoded, salt, iterations)
            if not hmac.compare_digest(candidate, digest):
                raise HTTPException(401, "Unable to sign in.")
        finally:
            self.hash_slots.release()
        now = self.clock()
        subject = secrets.token_urlsafe(24)
        session = Session(subject, self._csrf(subject), now + self.settings.session_seconds, now)
        return self._encode(session), session

    def get(self, token: str | None, *, touch: bool = True) -> Session | None:
        """Validate a cookie. Renewal on activity is done separately by ``renewed``."""
        if not self.settings.enabled or not token or len(token) > 512:
            return None
        session = self._decode(token)
        if session is None:
            return None
        now = self.clock()
        with self.lock:
            self._purge(now)
            if session.subject in self.revoked:
                return None
        if session.expires_at <= now or session.last_seen + self.settings.idle_seconds <= now:
            return None
        return session

    def renewed(self, session: Session) -> str | None:
        """A cookie re-issued with activity recorded now, or None if refreshed very recently."""
        now = self.clock()
        if now - session.last_seen < RENEW_AFTER_SECONDS:
            return None
        return self._encode(Session(session.subject, session.csrf, session.expires_at, now))

    def require(self, request: Request) -> Session:
        if not self.settings.enabled:
            raise HTTPException(503, "Private access has not been configured.")
        self.check_origin(request, write=request.method not in {"GET", "HEAD"})
        session = self.get(request.cookies.get(self.settings.cookie_name))
        if not session:
            raise HTTPException(401, "Sign in to use the private assistant.")
        if request.method not in {"GET", "HEAD"} and not hmac.compare_digest(
            request.headers.get("x-csrf-token", "").encode(), session.csrf.encode()
        ):
            raise HTTPException(403, "Refresh private access and try again.")
        request.state.assistant_cookie = self.renewed(session)
        request.state.assistant_cookie_expires = session.expires_at
        return session

    def logout(self, token: str | None) -> None:
        session = self._decode(token) if token and len(token) <= 512 else None
        if session is None:
            return
        with self.lock:
            self._purge(self.clock())
            if len(self.revoked) >= MAX_REVOKED:
                del self.revoked[min(self.revoked, key=self.revoked.get)]
            self.revoked[session.subject] = session.expires_at


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate an authentication hash locally; never put it in Git."
    )
    parser.add_argument("command", choices=["hash-password"])
    parser.parse_args()
    password = getpass.getpass("Private assistant passcode (8 digits) or passphrase: ")
    if password != getpass.getpass("Confirm passcode/passphrase: "):
        raise SystemExit("Passcodes did not match.")
    print(password_hash(password))


if __name__ == "__main__":
    main()
