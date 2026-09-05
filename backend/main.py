"""Web app for controlling an Advantage Air (MyPlace) heating system.

Serves both the API and the built frontend on one port, per the NAS
deployment pattern. LAN-only: the tablet API has no authentication, so this
must never be exposed to the internet.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .aircon import (
    FANS,
    MODES,
    STATES,
    ZONE_STATES,
    AirconClient,
    AirconError,
    summarize,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

TABLET_HOST = os.environ.get("MYPLACE_HOST", "")
TABLET_PORT = int(os.environ.get("MYPLACE_PORT", "2025"))
BUILD_SHA = os.environ.get("APP_BUILD_SHA", "unknown")
BUILD_TIME = os.environ.get("APP_BUILD_TIME", "unknown")

# Guard rails so a typo or a stuck button can't ask for something silly.
MIN_TEMP = float(os.environ.get("MYPLACE_MIN_TEMP", "16"))
MAX_TEMP = float(os.environ.get("MYPLACE_MAX_TEMP", "30"))
# Smallest change this system accepts. Some units do 0.5, this one only whole
# degrees - a half-degree target is silently ignored by the tablet, which looks
# to the user like the app not working.
TEMP_STEP = float(os.environ.get("MYPLACE_TEMP_STEP", "1"))

app = FastAPI(title="MyPlace Heating")
# The tablet's wifi is slow to wake, so the default timeout is generous.
TIMEOUT = float(os.environ.get("MYPLACE_TIMEOUT", "20"))
client = AirconClient(TABLET_HOST, TABLET_PORT, TIMEOUT) if TABLET_HOST else None

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _client() -> AirconClient:
    if client is None:
        raise HTTPException(
            503,
            "MYPLACE_HOST is not set. Put the tablet's IP address in "
            "docker-compose.yml and restart.",
        )
    return client


def _clamp(temp: float) -> float:
    """Keep the target in range and on a step the system will actually accept."""
    temp = max(MIN_TEMP, min(MAX_TEMP, temp))
    return round(temp / TEMP_STEP) * TEMP_STEP


class TempBody(BaseModel):
    temp: float = Field(..., description="Target temperature in Celsius")


class ModeBody(BaseModel):
    mode: str


class FanBody(BaseModel):
    fan: str


class ZoneBody(BaseModel):
    state: str | None = None
    setTemp: float | None = None
    value: int | None = None


@app.get("/api/version")
async def version():
    return {
        "sha": BUILD_SHA,
        "buildTime": BUILD_TIME,
        "tablet": f"{TABLET_HOST}:{TABLET_PORT}" if TABLET_HOST else None,
        "minTemp": MIN_TEMP,
        "maxTemp": MAX_TEMP,
        "tempStep": TEMP_STEP,
    }


@app.get("/api/status")
async def status():
    try:
        return summarize(await _client().get_system_data())
    except AirconError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/diagnose")
async def diagnose():
    """Say *why* the tablet cannot be reached, from where this app runs.

    'Asleep' and 'this host cannot route to the tablet' look identical to the
    UI but need completely different fixes, so distinguish them here rather
    than making the user guess.
    """
    import socket

    result: dict[str, Any] = {"tablet": TABLET_HOST, "port": TABLET_PORT}

    # Can we open a TCP connection at all?
    sock = socket.socket()
    # The tablet's wifi can take seconds to wake; a short probe would report a
    # false failure on a link that actually works.
    sock.settimeout(TIMEOUT)
    try:
        sock.connect((TABLET_HOST, TABLET_PORT))
        result["tcp"] = "open"
    except socket.timeout:
        result["tcp"] = "timeout"
    except OSError as exc:
        result["tcp"] = f"error: {exc}"
    finally:
        sock.close()

    if result["tcp"] == "open":
        try:
            await _client().get_system_data()
            result["api"] = "ok"
            result["diagnosis"] = "the tablet is reachable and answering"
        except AirconError as exc:
            result["api"] = str(exc)
            result["diagnosis"] = "the tablet answers but is not returning data"
    elif result["tcp"] == "timeout":
        result["diagnosis"] = (
            "no reply from the tablet. It is asleep, or this machine is on a "
            "network segment that cannot reach it."
        )
    else:
        result["diagnosis"] = (
            "the connection was refused or the address is unreachable from "
            "this machine - a routing or firewall problem, not a sleeping tablet"
        )
    return result


@app.get("/api/raw")
async def raw():
    """Unmodified getSystemData — handy for checking what your unit supports."""
    try:
        return await _client().get_system_data()
    except AirconError as exc:
        raise HTTPException(502, str(exc)) from exc


# The tablet applies a change a moment after acknowledging it, so a status read
# taken immediately still shows the old value. Wait briefly before reading back.
SETTLE_SECONDS = float(os.environ.get("MYPLACE_SETTLE", "1.5"))


async def _send(info: dict) -> dict:
    c = _client()
    try:
        ac = summarize(await c.get_system_data())["acId"]
        await c.set_aircon({ac: {"info": info}})
        await asyncio.sleep(SETTLE_SECONDS)
        return summarize(await c.get_system_data())
    except AirconError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/power/{state}")
async def power(state: str):
    if state not in STATES:
        raise HTTPException(400, f"state must be one of {sorted(STATES)}")
    # countDownToOff/On are cleared so a previously set timer can't override
    # an explicit tap. appactions/z.java:95 sends them the same way.
    return await _send({"state": state, "countDownToOff": "0", "countDownToOn": "0"})


@app.post("/api/mode")
async def mode(body: ModeBody):
    if body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {sorted(MODES)}")
    return await _send({"mode": body.mode})


@app.post("/api/temp")
async def temp(body: TempBody):
    return await _send({"setTemp": _clamp(body.temp)})


@app.post("/api/fan")
async def fan(body: FanBody):
    if body.fan not in FANS:
        raise HTTPException(400, f"fan must be one of {sorted(FANS)}")
    return await _send({"fan": body.fan})


@app.post("/api/heat-on")
async def heat_on(body: TempBody):
    """One tap: power on, mode heat, target temperature — a single request."""
    return await _send(
        {
            "state": "on",
            "mode": "heat",
            "setTemp": _clamp(body.temp),
            "countDownToOff": "0",
            "countDownToOn": "0",
        }
    )


@app.post("/api/zone/{zone_id}/temp")
async def zone_temp(zone_id: str, body: TempBody):
    """Set one room's target temperature.

    On a zoned system this is what actually controls the heating: the unit
    follows the zone named by `myZone`, so the system-level setTemp alone
    changes nothing the user can feel.
    """
    return await _zone_change(zone_id, {"setTemp": _clamp(body.temp)})


@app.post("/api/zone/{zone_id}")
async def zone(zone_id: str, body: ZoneBody):
    change: dict = {}
    if body.state is not None:
        if body.state not in ZONE_STATES:
            raise HTTPException(400, f"zone state must be one of {sorted(ZONE_STATES)}")
        change["state"] = body.state
    if body.setTemp is not None:
        change["setTemp"] = _clamp(body.setTemp)
    if body.value is not None:
        change["value"] = max(0, min(100, body.value))
    if not change:
        raise HTTPException(400, "nothing to change")
    return await _zone_change(zone_id, change)


async def _zone_change(zone_id: str, change: dict) -> dict:
    c = _client()
    try:
        ac = summarize(await c.get_system_data())["acId"]
        await c.set_aircon({ac: {"zones": {zone_id: change}}})
        await asyncio.sleep(SETTLE_SECONDS)
        return summarize(await c.get_system_data())
    except AirconError as exc:
        raise HTTPException(502, str(exc)) from exc


# Mounted last so /api/* wins. Falls back to index.html for client-side routes.
if FRONTEND.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        candidate = FRONTEND / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND / "index.html")
