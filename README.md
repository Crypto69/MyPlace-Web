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

## Security

The tablet API has **no authentication** — anyone who can reach it on the
network can control the heating. This app adds none, so:

- Do not forward port 8322 through the router.
- Do not put it on a public-facing reverse proxy.
- For access from outside the house, use Tailscale (already on the NAS) so
  the app stays on the private tailnet.

## Layout

```
backend/     FastAPI app; aircon.py is the tablet client
frontend/    Vue 3 UI (built into the image, served by the backend)
probe/       discover.sh (find the tablet), fake_tablet.py (test double)
docs/API.md  the reverse-engineered protocol, with source citations
analysis/    decompiled APK — gitignored, regenerate with jadx
```
