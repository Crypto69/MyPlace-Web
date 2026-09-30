"""Web app for controlling an Advantage Air (MyPlace) heating system.

Serves both the API and the built frontend on one port, per the NAS
deployment pattern. LAN-only: the tablet API has no authentication, so this
must never be exposed to the internet.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

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

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "dist"

# The tablet's DHCP address can change when it reboots, so the address is
# editable from the UI. A saved address wins over MYPLACE_HOST, which is only
# the starting value; it lives in data/ (a mounted volume) so it survives
# redeploys.
SETTINGS = Path(os.environ.get("MYPLACE_DATA", ROOT / "data")) / "settings.json"


def _load_host() -> str:
    try:
        saved = json.loads(SETTINGS.read_text()).get("host", "")
        if saved:
            return saved
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable %s: %s", SETTINGS, exc)
    return os.environ.get("MYPLACE_HOST", "")


def _save_host(host: str) -> None:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps({"host": host}))
    tmp.replace(SETTINGS)  # atomic, so a crash mid-write can't lose the address


tablet_host = _load_host()
client = AirconClient(tablet_host, TABLET_PORT, TIMEOUT) if tablet_host else None


def _client() -> AirconClient:
    if client is None:
        raise HTTPException(
            503,
            "The tablet's address is not set. Enter it under "
            "'Tablet address' at the bottom of the page.",
        )
    return client


_HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                       r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        # All-digit dotted strings that failed above are mistyped IPs, not names.
        return bool(_HOSTNAME.match(host)) and not re.fullmatch(r"[\d.]+", host)


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


class TabletBody(BaseModel):
    host: str


class MyZoneBody(BaseModel):
    zone: str = Field(..., description="Zone id, like z01")


class ZoneBody(BaseModel):
    state: str | None = None
    setTemp: float | None = None
    value: int | None = None


@app.get("/api/version")
async def version():
    return {
        "sha": BUILD_SHA,
        "buildTime": BUILD_TIME,
        "tablet": f"{tablet_host}:{TABLET_PORT}" if tablet_host else None,
        "minTemp": MIN_TEMP,
        "maxTemp": MAX_TEMP,
        "tempStep": TEMP_STEP,
    }


@app.get("/api/tablet")
async def get_tablet():
    return {"host": tablet_host, "port": TABLET_PORT}


@app.post("/api/tablet")
async def set_tablet(body: TabletBody):
    """Point the app at a new tablet address, and remember it."""
    global client, tablet_host
    host = body.host.strip()
    if not _valid_host(host):
        raise HTTPException(400, f"'{host}' is not an IP address, like 192.168.1.20")
    _save_host(host)
    tablet_host = host
    client = AirconClient(host, TABLET_PORT, TIMEOUT)
    log.info("tablet address changed to %s", host)
    return {"host": tablet_host, "port": TABLET_PORT}


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

    host = _client().host
    result: dict[str, Any] = {"tablet": host, "port": TABLET_PORT}

    # Can we open a TCP connection at all?
    sock = socket.socket()
    # The tablet's wifi can take seconds to wake; a short probe would report a
    # false failure on a link that actually works.
    sock.settimeout(TIMEOUT)
    try:
        sock.connect((host, TABLET_PORT))
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


@app.post("/api/myzone")
async def my_zone(body: MyZoneBody):
    """Choose which room the unit follows (myZone).

    Only a room with a temperature sensor can lead: the unit steers by that
    room's reading. The room is opened in the same request, because a closed
    damper would leave the unit chasing a temperature it cannot change.
    """
    c = _client()
    try:
        status = summarize(await c.get_system_data())
        z = next((z for z in status["zones"] if z["id"] == body.zone), None)
        if z is None:
            raise HTTPException(404, f"no room called {body.zone}")
        if not z["hasSensor"] or z["number"] is None:
            raise HTTPException(400, f"{z['name']} has no temperature sensor, so it cannot control the heating")
        change: dict = {"info": {"myZone": z["number"]}}
        if z["state"] != "open":
            change["zones"] = {z["id"]: {"state": "open"}}
        await c.set_aircon({status["acId"]: change})
        await asyncio.sleep(SETTLE_SECONDS)
        return summarize(await c.get_system_data())
    except AirconError as exc:
        raise HTTPException(502, str(exc)) from exc


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
