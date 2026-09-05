# MyPlace / Advantage Air — Local HTTP API

Reverse-engineered from `myplace-15-1548.apk` (package `com.air.advantage.myair5`).
Every fact below is cited to the decompiled source it came from.

## Transport

```
http://<TABLET_IP>:2025/<endpoint>?json=<url-encoded JSON>
```

- **Port `2025`** — `res/values/strings.xml`: `<string name="communication_endpoint">2025</string>`,
  read at runtime in `data/p.java:55`.
- **URL shape** — `e4.java:393-398` builds `ipAddress + ":" + port + "/" + messageRequest + "?" + messageParameters`.
- **Plain HTTP, no authentication, no TLS.** The server is NanoHTTPD embedded in the wall tablet
  (`webserver/a.java`, `webserver/c.java`).
- **`json=` must be the whole query string.** `libraryairconlightjson/b.java:90-92` — if the
  parameters start with `json=`, everything after it is URL-decoded as one JSON blob. It does
  *not* fall back to `&`-splitting in that case, so `json=` must come first and be the only param.
- Requests are `GET` (except `setBackupDataToRestore`, which POSTs a form body — `e4.java:399-420`).

## Endpoints

The tablet's own handler whitelists these write endpoints (`webserver/c.java:97`):

```
changeName        setSystemData   setClock        setZoneData     setZoneTimer
setScheduleData   setLight        setLightName    setLightToGroup setLightScene
runLightScene     setLightGroupName setLightGroup setAircon       setSnapShot
setMySystem       setThing        setGroupThing   setGroupThingName
setNewGroupThingName setThingToGroupThing setThingToNewGroupThing
runScene          setScene        setSensor       setBackupDataToRestore  setMonitor
```

Read endpoints seen in the same class: `getSystemData`, `getLights`.

### The two that matter for heating control

| Endpoint | Purpose |
|---|---|
| `GET /getSystemData` | Full state: every aircon unit, every zone, temps, modes. No params. |
| `GET /setAircon?json=…` | Change power, mode, target temp, fan, and per-zone state. |

## `setAircon` payload

Shape confirmed by a literal in `appactions/z.java:95`:

```json
{"ac1": {"info": {"state": "on", "countDownToOff": "0", "countDownToOn": "0"}}}
```

General form — send only the fields you want to change:

```json
{
  "ac1": {
    "info":  { "state": "on", "mode": "heat", "setTemp": 21.0, "fan": "auto" },
    "zones": { "z01": { "state": "open", "value": 100, "setTemp": 21.0 } }
  }
}
```

### Enum values (serialized by **name**, not number)

| Field | Values | Source |
|---|---|---|
| `state` (aircon) | `off`, `on` | `libraryairconlightjson/j.java` |
| `mode` | `cool`, `heat`, `vent`, `auto`, `dry`, `myauto` | `libraryairconlightjson/a.java` |
| `fan` | `off`, `low`, `medium`, `high`, `auto`, `autoAA` | `libraryairconlightjson/e.java` |
| `state` (zone) | `close`, `open` | `libraryairconlightjson/l.java` |

**For heating: `mode` = `heat`.**

### Aircon `info` fields (`data/e.java`)

Writable / useful: `state`, `mode`, `setTemp` (Float), `fan`, `myZone` (Int),
`countDownToOff`, `countDownToOn` (Int, minutes).

Read-only status: `name`, `noOfZones`, `noOfConstants`, `airconErrorCode`,
`filterCleanStatus`, `climateControlModeEnabled`, `climateControlModeIsRunning`,
`myAutoModeEnabled`, `myAutoModeIsRunning`, `myAutoHeatTargetTemp`,
`myAutoCoolTargetTemp`, `quietNightModeEnabled`, `quietNightModeIsRunning`,
`freshAirStatus`, `uid`, `unitType`, `cbType`, firmware revs.

### Zone fields (`data/z0.java`)

Writable: `state` (`open`/`close`), `value` (Int, damper % — used when the zone has no sensor),
`setTemp` (Float — used when the zone *has* a temperature sensor).

Read-only: `name`, `number`, `measuredTemp`, `type` (0 = damper-only, >0 = has sensor),
`minDamper`, `maxDamper`, `motion`, `motionConfig`, `rssi`, `error`, `sensorUid`.

> `type` decides which control applies: a zone with a sensor follows `setTemp`;
> a zone without one follows `value` (damper percentage).

## Worked examples

```bash
TABLET=192.168.1.115

# Read everything
curl -s "http://$TABLET:2025/getSystemData" | jq .

# Heating on at 21°C
curl -s -G "http://$TABLET:2025/setAircon" \
  --data-urlencode 'json={"ac1":{"info":{"state":"on","mode":"heat","setTemp":21}}}'

# Turn off
curl -s -G "http://$TABLET:2025/setAircon" \
  --data-urlencode 'json={"ac1":{"info":{"state":"off"}}}'

# Open zone 1, set it to 22°C
curl -s -G "http://$TABLET:2025/setAircon" \
  --data-urlencode 'json={"ac1":{"zones":{"z01":{"state":"open","setTemp":22}}}}'
```

`curl -G --data-urlencode` is important: it URL-encodes the JSON and keeps `json=` as the
sole query parameter, which is what the parser requires.

## Naming conventions

- Aircon units: `ac1`, `ac2`, … (`appactions/z.java:95`)
- Zones: `z01`, `z02`, … zero-padded to two digits (`data/c.java` `getOrMakeDataZone`)

## Caveats

- No auth means anyone on your LAN can control the heating. Keep this off the public internet.
- Field availability varies by system generation (MyAir4 / MyAir5 / e-zone / Zone10).
  `getSystemData` on *your* unit is the ground truth — check it before relying on any field.
- `setTemp` is a Float; systems typically accept 0.5° steps within a 16–32°C range.
