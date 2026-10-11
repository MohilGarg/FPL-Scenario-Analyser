"""Single-user, server-held opaque sessions. No password or signing key defaults."""

from __future__ import annotations

import getpass
import hashlib
import hmac
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from threading import BoundedSemaphore, Lock
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

ITERATIONS = 600_000


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

    @classmethod
    def from_environment(cls) -> AuthSettings:
        return cls(
            origin=(
                os.getenv("FPL_ASSISTANT_ORIGIN") or os.getenv("RENDER_EXTERNAL_URL", "")
            ).rstrip("/"),
            encoded_password=os.getenv("FPL_ASSISTANT_PASSWORD_HASH", ""),
            development_http=os.getenv("FPL_ASSISTANT_DEV_HTTP", "") == "1",
        )

    @property
    def enabled(self) -> bool:
        try:
            _parse_hash(self.encoded_password)
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


class SessionStore:
    """One process, bounded in-memory sessions. Restarts revoke all sessions."""

    def __init__(self, settings: AuthSettings | None = None, *, clock=time.time) -> None:
        self.settings = settings or AuthSettings.from_environment()
        self.clock = clock
        self.lock = Lock()
        self.hash_slots = BoundedSemaphore(2)
        self.sessions: dict[str, Session] = {}
        self.attempts: dict[str, deque[float]] = {}
        self.global_attempts: deque[float] = deque()

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
        token = secrets.token_urlsafe(32)
        session = Session(
            secrets.token_urlsafe(24),
            secrets.token_urlsafe(32),
            now + self.settings.session_seconds,
            now,
        )
        with self.lock:
            self._purge(now)
            if len(self.sessions) >= 16:
                del self.sessions[next(iter(self.sessions))]
            self.sessions[self._token_key(token)] = session
        return token, session

    @staticmethod
    def _token_key(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _purge(self, now: float) -> None:
        for key, session in list(self.sessions.items()):
            if session.expires_at <= now or session.last_seen + self.settings.idle_seconds <= now:
                del self.sessions[key]

    def get(self, token: str | None, *, touch: bool = True) -> Session | None:
        if not self.settings.enabled or not token or len(token) > 128:
            return None
        now = self.clock()
        with self.lock:
            self._purge(now)
            session = self.sessions.get(self._token_key(token))
            if session and touch:
                session.last_seen = now
            return session

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
        return session

    def logout(self, token: str | None) -> None:
        if token:
            with self.lock:
                self.sessions.pop(self._token_key(token), None)


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
