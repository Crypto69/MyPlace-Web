"""Client for the Advantage Air (MyPlace) local HTTP API.

Protocol reverse-engineered from myplace-15-1548.apk; see docs/API.md for the
citations. In short: an unauthenticated NanoHTTPD server on the wall tablet,

    http://<ip>:2025/<endpoint>?json=<url-encoded JSON>

with `json=` required to be the whole query string.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Enum values the tablet accepts, serialized by name (libraryairconlightjson/).
MODES = {"cool", "heat", "vent", "auto", "dry", "myauto"}
FANS = {"off", "low", "medium", "high", "auto", "autoAA"}
STATES = {"on", "off"}
ZONE_STATES = {"open", "close"}

# The tablet is a small embedded device: serialize requests so a burst of taps
# can't overlap and confuse it.
_lock = asyncio.Lock()

# It also drops the odd request while applying a change; retry before giving up.
RETRIES = 5
RETRY_DELAY = 0.8


class AirconError(RuntimeError):
    """The tablet could not be reached or rejected the request."""


class AirconClient:
    def __init__(self, host: str, port: int = 2025, timeout: float = 8.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def _get(self, endpoint: str, params: dict[str, str] | None = None) -> dict:
        """One request, retried on the tablet's occasional hiccups.

        The tablet is a small embedded device and will sometimes drop a
        connection or return a truncated body while it is busy applying a
        change. Those are transient, so retry briefly rather than surfacing
        an error to someone who just wants the heating on.
        """
        url = f"{self.base}/{endpoint}"
        last: Exception | None = None

        for attempt in range(RETRIES):
            if attempt:
                await asyncio.sleep(RETRY_DELAY * attempt)
            async with _lock:
                try:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        resp = await client.get(url, params=params)
                        resp.raise_for_status()
                        text = resp.text
                except httpx.HTTPError as exc:
                    last = exc
                    log.warning("tablet request failed (%s/%s): %s", attempt + 1, RETRIES, exc)
                    continue

            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                # A truncated body: the tablet was mid-update. Worth retrying.
                last = exc
                log.warning("tablet returned partial JSON (%s/%s)", attempt + 1, RETRIES)
                continue

            # For a short window after a change, the tablet answers 200 with a
            # bare "{}" while it rebuilds its state. That parses fine but has no
            # data in it, so it has to be caught here rather than by the JSON
            # decoder, and retried like any other transient failure.
            if endpoint == "getSystemData" and "aircons" not in data:
                last = AirconError("tablet returned an empty state")
                log.warning("tablet state not ready (%s/%s)", attempt + 1, RETRIES)
                continue

            return data

        raise AirconError(
            f"the heating system at {self.host} is not responding properly "
            f"({type(last).__name__})"
        ) from last

    async def get_system_data(self) -> dict:
        """Full system state: every aircon unit, every zone."""
        return await self._get("getSystemData")

    async def set_aircon(self, payload: dict[str, Any]) -> dict:
        """Send a setAircon change.

        `json=` must be the entire query string and URL-encoded; httpx's params
        handling does exactly that. separators=(",", ":") keeps the URL short.
        """
        blob = json.dumps(payload, separators=(",", ":"))
        log.info("setAircon %s", blob)
        return await self._get("setAircon", {"json": blob})


def _ac_key(system: dict) -> str:
    """Name of the first aircon unit, usually 'ac1'."""
    acs = system.get("aircons") or {}
    if not acs:
        raise AirconError("no aircon units found in getSystemData")
    return sorted(acs)[0]


def summarize(system: dict) -> dict:
    """Reduce getSystemData to just what the UI needs.

    Field availability differs across system generations (MyAir4/5, e-zone), so
    every lookup is defensive — a missing key means "unknown", not a crash.
    """
    key = _ac_key(system)
    ac = system["aircons"][key]
    info = ac.get("info") or {}

    zones = []
    for zid, z in sorted((ac.get("zones") or {}).items()):
        # type 0 = damper-only (control by percentage); >0 = has a temperature
        # sensor (control by setTemp). data/z0.java
        has_sensor = (z.get("type") or 0) > 0
        zones.append(
            {
                "id": zid,
                "name": z.get("name") or zid,
                "state": z.get("state"),
                "value": z.get("value"),
                "setTemp": z.get("setTemp"),
                "measuredTemp": z.get("measuredTemp"),
                "hasSensor": has_sensor,
            }
        )

    return {
        "acId": key,
        "name": info.get("name") or "Heating",
        "state": info.get("state"),
        "mode": info.get("mode"),
        "setTemp": info.get("setTemp"),
        "fan": info.get("fan"),
        "myZone": info.get("myZone"),
        "errorCode": info.get("airconErrorCode"),
        "filterClean": info.get("filterCleanStatus"),
        "zones": zones,
    }
