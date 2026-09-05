"""Client for the MyPlace cloud (remote) path.

Advantage Air do not expose a REST API. The phone app writes commands into a
Firebase Realtime Database that the wall tablet is subscribed to, and reads
state back out of the same database. See docs/CLOUD-API.md for the citations.

    this app --write--> Firebase RTDB --> wall tablet --> heating

Auth mirrors the app (n2/t.java): there are no user credentials, the client
registers its own <uuid>@aair.com.au account on first run and keeps the
refresh token.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

# res/values/strings.xml
API_KEY = "AIzaSyDjaH5njhFcf-4o6xIFgPRfkxZ9Op_rYWc"
DB_URL = "https://myplacev3.firebaseio.com"

IDENTITY = "https://identitytoolkit.googleapis.com/v1"
SECURETOKEN = "https://securetoken.googleapis.com/v1"


class CloudError(RuntimeError):
    """Sign-in failed, or the cloud rejected a request."""


class CloudClient:
    """Talks to the MyPlace Firebase database as a self-registered account.

    `tsp_id` is the wall tablet's id, shown on its Setup -> Advanced Info
    screen. Credentials are persisted so the same account is reused across
    restarts; re-registering on every boot would litter their user table.
    """

    def __init__(self, tsp_id: str, cred_path: Path, timeout: float = 15.0):
        self.tsp_id = tsp_id
        self.cred_path = cred_path
        self.timeout = timeout
        self._id_token: str | None = None
        self._refresh_token: str | None = None
        self._uid: str | None = None
        self._expires_at = 0.0

    # ---- credentials -------------------------------------------------

    def _load(self) -> dict | None:
        if self.cred_path.is_file():
            try:
                return json.loads(self.cred_path.read_text())
            except (OSError, json.JSONDecodeError):
                log.warning("credential file unreadable; re-registering")
        return None

    def _save(self, creds: dict) -> None:
        self.cred_path.parent.mkdir(parents=True, exist_ok=True)
        self.cred_path.write_text(json.dumps(creds, indent=2))
        # Contains a password and a refresh token.
        self.cred_path.chmod(0o600)

    def _post(self, url: str, payload: dict) -> dict:
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.post(url, json=payload, params={"key": API_KEY})
                body = r.json()
                if r.status_code >= 400:
                    msg = body.get("error", {}).get("message", r.text[:200])
                    raise CloudError(f"{url.rsplit('/', 1)[-1]} failed: {msg}")
                return body
        except httpx.HTTPError as exc:
            raise CloudError(f"cannot reach Google identity service: {exc}") from exc

    # ---- auth --------------------------------------------------------

    def _register(self) -> dict:
        """Create an account the way the app does (n2/t.java:504-510)."""
        creds = {
            "email": uuid.uuid4().hex + "@aair.com.au",
            "password": uuid.uuid4().hex,
        }
        log.info("registering a new cloud account")
        self._post(
            f"{IDENTITY}/accounts:signUp",
            {**creds, "returnSecureToken": True},
        )
        self._save(creds)
        return creds

    def _sign_in(self) -> None:
        creds = self._load() or self._register()
        body = self._post(
            f"{IDENTITY}/accounts:signInWithPassword",
            {**creds, "returnSecureToken": True},
        )
        self._apply(body["idToken"], body["refreshToken"], body["expiresIn"])
        self._uid = body["localId"]
        # Keep the uid alongside the credentials: it is this client's device id
        # and forms part of the command path.
        self._save({**creds, "uid": self._uid})

    def _apply(self, id_token: str, refresh_token: str, expires_in: str | int) -> None:
        self._id_token = id_token
        self._refresh_token = refresh_token
        # Refresh a minute early so a request never races the expiry.
        self._expires_at = time.time() + int(expires_in) - 60

    def _refresh(self) -> None:
        body = self._post(
            f"{SECURETOKEN}/token",
            {"grant_type": "refresh_token", "refresh_token": self._refresh_token},
        )
        self._apply(body["id_token"], body["refresh_token"], body["expires_in"])

    def _token(self) -> str:
        if self._id_token is None:
            self._sign_in()
        elif time.time() >= self._expires_at:
            try:
                self._refresh()
            except CloudError:
                log.info("refresh failed; signing in again")
                self._sign_in()
        assert self._id_token is not None
        return self._id_token

    @property
    def uid(self) -> str:
        if self._uid is None:
            self._token()
        assert self._uid is not None
        return self._uid

    # ---- database ----------------------------------------------------

    def _db(self, method: str, path: str, payload: Any = None) -> Any:
        url = f"{DB_URL}/{path.strip('/')}.json"
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.request(
                    method, url, params={"auth": self._token()}, json=payload
                )
                if r.status_code >= 400:
                    raise CloudError(f"database {method} {path} -> {r.status_code} {r.text[:200]}")
                return r.json()
        except httpx.HTTPError as exc:
            raise CloudError(f"cannot reach the MyPlace database: {exc}") from exc

    def _command_path(self) -> str:
        """commands/<first char of tspId>/<tspId>/<deviceId>

        Sharded by the tsp id's first character (FirebaseComms.java:1503-1509).
        """
        return f"commands/{self.tsp_id[0]}/{self.tsp_id}/{self.uid}"

    def send(self, request: str, message: str) -> None:
        """Queue one command for the tablet.

        The value is the same request line the local API takes, so the payloads
        in docs/API.md carry over unchanged. The key is a zero-padded
        millisecond timestamp: Firebase orders children lexicographically, and
        the %015d padding is what keeps them in chronological order
        (FirebaseComms.java:1495-1501, 2019-2029).
        """
        key = "-" + f"{int(time.time() * 1000):015d}"
        value = f"{request}?{message}"
        log.info("cloud command %s", value)
        self._db("PUT", f"{self._command_path()}/{key}", value)

    def get_state(self) -> dict:
        """Read the state the tablet mirrors into the database."""
        data = self._db("GET", f"data/{self.tsp_id[0]}/{self.tsp_id}")
        if not data:
            raise CloudError(
                "the cloud has no state for this system - check the TSP ID, and "
                "that the wall tablet is powered on and connected"
            )
        return data
