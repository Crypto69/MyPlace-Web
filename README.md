# MyPlace Heating — web control

A small web app for controlling an Advantage Air (MyPlace / MyAir) ducted
heating system from a browser on the home network. Built for hands-free use
with a head mouse: comfortable targets, no dragging, no swiping, no
long-presses.

Runs in Docker on the TerraMaster NAS. **LAN only** — see Security.

## How it works

The MyPlace wall tablet runs an unauthenticated HTTP API on port 2025. This
app talks to it and serves a simple UI. Protocol reverse-engineered from the
official APK — see [docs/API.md](docs/API.md), which cites the decompiled
source for every claim.

```
browser  ->  this app (NAS :8322)  ->  wall tablet (:2025)  ->  heating
```

## Try it first, on your own machine

Docker Desktop running, then:

```bash
./try-locally.sh                  # demo against a fake heater
./try-locally.sh 192.168.1.50     # against the real wall tablet
```

Open `http://localhost:8322`. Stop it with `docker compose down`.

Without an IP it starts a fake tablet, so the app can be tried with no heating
system involved. This is the same image the NAS runs.

## First-time setup

**1. Find the tablet's IP address**, from any machine on the home network:

```bash
bash probe/discover.sh
```

It prints something like `FOUND: http://192.168.1.50:2025`. Give the tablet a
DHCP reservation in the router so the address does not change.

**2. Deploy to the NAS** (follows the standard NAS deployment pattern):

```bash
ssh big-kahuna-stor
cd /Volume5/<your-share>
git clone https://github.com/Crypto69/myplace.git
cd myplace
chmod -R a+rX .                              # share ACLs strip modes
echo 'MYPLACE_HOST=192.168.1.50' > .env      # the IP from step 1
./deploy.sh
```

Then open `http://big-kahuna-stor:8322` in a browser.

## Updating

```bash
ssh big-kahuna-stor
cd /Volume5/<your-share>/myplace
git pull
chmod -R a+rX .          # ACLs strip modes on every pull
./deploy.sh
```

`.env` is gitignored, so the tablet's IP survives updates.

## Local development

Three terminals, no NAS and no real heater needed:

```bash
python3 probe/fake_tablet.py 2025                              # 1: fake tablet
MYPLACE_HOST=127.0.0.1 uvicorn backend.main:app --port 8322    # 2: backend
cd frontend && npm run dev                                     # 3: frontend
```

The fake tablet mimics a three-zone MyAir5 and logs every command it receives.

To point at the real system instead, set `MYPLACE_HOST` to the tablet's IP.

## API

| Method | Path | Body | Does |
|---|---|---|---|
| GET | `/api/status` | – | Current state, tidied for the UI |
| GET | `/api/raw` | – | Unmodified `getSystemData` |
| GET | `/api/version` | – | Build SHA, tablet address, temp limits |
| POST | `/api/heat-on` | `{"temp": 21}` | On + heat + temperature, one request |
| POST | `/api/power/on\|off` | – | Power |
| POST | `/api/mode` | `{"mode": "heat"}` | cool/heat/vent/auto/dry/myauto |
| POST | `/api/temp` | `{"temp": 21}` | Target temperature |
| POST | `/api/fan` | `{"fan": "auto"}` | off/low/medium/high/auto/autoAA |
| POST | `/api/zone/z01` | `{"state": "open"}` | Per-room control |

Temperatures are clamped to `MYPLACE_MIN_TEMP`–`MYPLACE_MAX_TEMP` (16–30 by
default) and rounded to the 0.5° step the system uses.

## Accessibility

Built for driving with a gyroscopic head mouse:

- Moderate targets — 68px base, 96px for the primary actions. Above the 44px
  WCAG minimum and easy to hit, without the waste of oversized controls.
- Clear gaps, so a small overshoot lands on nothing rather than on the wrong
  control.
- Click-only. No drag, swipe, or long-press anywhere in the app.
- High-contrast focus ring for keyboard and switch driving.
- Every control is a real `<button>`, so tab order and screen readers work.
- One-click presets for the common cases, avoiding repeated +/- clicks.
- Honours `prefers-reduced-motion`.

To resize everything, change `--gap`, `--radius` and the `font-size` on `body`
in `frontend/src/style.css`; the rest is in `rem` and scales with them.

## The wall tablet must stay awake

The local API is served by the MyPlace app running on the wall tablet, so it
only answers while that tablet is awake and on the network. When the tablet
sleeps it appears to leave the network entirely - it stops answering ping, its
MAC never resolves, and port 2025 is closed.

On the tablet (an ordinary Android device), set:

- **Settings -> Display -> Screen timeout: Never**
- **Settings -> Wi-Fi -> Advanced -> Keep Wi-Fi on during sleep: Always**

It is wall-powered, so there is no battery cost. If it does go to sleep, the
app says so plainly rather than showing a connection error, and reconnects by
itself once the tablet is awake.

## Zoned systems: which temperature actually matters

A zoned system has a system-level `setTemp` *and* a target per zone. The unit
follows the single zone named by `info.myZone` - changing the system-level
target alone does nothing the user can feel.

The UI therefore shows and changes the **controlling room's** target, names
that room, and tags it in the room list. `summarize()` marks it with
`isControlling`; if no zone claims it, the app falls back to the system-level
target so an unzoned system still behaves sensibly.

## Security

The tablet API has **no authentication** — anyone who can reach it on the
network can control the heating. This app adds none, so:

- Do not forward port 8322 through the router.
- Do not put it on a public-facing reverse proxy.
- For access from outside the house, use Tailscale (already on the NAS) so
  the app stays on the private tailnet.

## Layout

```
try-locally.sh  run it on your own machine, with or without a real heater
backend/     FastAPI app; aircon.py is the tablet client
frontend/    Vue 3 UI (built into the image, served by the backend)
probe/       discover.sh (find the tablet), fake_tablet.py (test double)
docs/API.md  the reverse-engineered protocol, with source citations
analysis/    decompiled APK — gitignored, regenerate with jadx
```
