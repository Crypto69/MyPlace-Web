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
        url = f"{self.base}/{endpoint}"
        async with _lock:
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    text = resp.text
            except httpx.HTTPError as exc:
                raise AirconError(f"cannot reach the tablet at {self.host}: {exc}") from exc

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AirconError(f"tablet returned non-JSON: {text[:200]!r}") from exc

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
