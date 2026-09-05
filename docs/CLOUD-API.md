# MyPlace cloud (remote) API

Reverse-engineered from `myplace-15-1548.apk`. This is the path the phone/iPad
app uses when it is not on the same network as the wall tablet — the one that
prints `connected via: internet` on the app's Advanced Info screen.

For the local (LAN) protocol see [API.md](API.md). **Prefer local when it is
available**: it keeps working if Advantage Air's servers go away.

## Architecture

It is not a REST API. It is a **Firebase Realtime Database used as a message
queue**, with the wall tablet as the only thing that actually talks to the
heating:

```
this app  --write command-->  Firebase RTDB  --tablet is subscribed-->  wall tablet
this app  <--read state-----  Firebase RTDB  <--tablet publishes------  wall tablet
```

The tablet must be online for anything to happen; the cloud only relays.

## Firebase project

From `res/values/strings.xml`:

| Key | Value |
|---|---|
| `project_id` | `myplacev3` |
| `firebase_database_url` | `https://myplacev3.firebaseio.com` |
| `google_api_key` | `AIzaSyDjaH5njhFcf-4o6xIFgPRfkxZ9Op_rYWc` |
| `google_app_id` | `1:115831800084:android:1fc553fc8c4d20f9` |
| `gcm_defaultSenderId` | `115831800084` |

Sibling databases exist per product line (`FirebaseComms.java:74-80`):
`myplacev3ghome`, `ezonev3ghome`, `zone10ev3ghome` — the `ghome` ones back the
Google Home / Alexa integrations.

## Authentication

`n2/t.java`. The app does **not** ask the user for credentials. On first run it
generates its own account (`t.java:504-510`):

```
email    = <UUID with dashes stripped>@aair.com.au
password = <UUID with dashes stripped>
```

and then uses `signInWithEmailAndPassword`, refreshing later via
`signInWithCustomToken`. The credentials are kept in `SharedPreferencesStore`.

So a replacement client must either **register its own account the same way**,
or reuse the stored credentials from an existing install.

## Database layout

Path constants, `FirebaseComms.java:50-77`:

| Constant | Path | Holds |
|---|---|---|
| `P` | `commands/` | commands **to** a system |
| `Q` | `data/` | system state **from** a tablet |
| `R` | `tspLogs/` | tablet logs |
| `S` | `lights/` | light state |
| `T` | `tspidList/` | known tablet ids |
| `U` | `/links/` | account ↔ system pairings |
| `V` | `/linksByTspId/` | the same, indexed by tablet |

### Sharding

Command paths are sharded by the **first character of the TSP ID**
(`FirebaseComms.java:1503-1509`):

```
commands/<first char of tspId>/<tspId>
```

For TSP ID `hHq5SJCSXOTdBBzix4sQzNPw8Uo1` that is
`commands/h/hHq5SJCSXOTdBBzix4sQzNPw8Uo1`.

## Sending a command

`FirebaseComms.p0()`, line 2019-2029. A command is a **child node whose key is a
timestamp and whose value is a string**:

```
key   = "-" + String.format("%015d", System.currentTimeMillis() + clockSkew)
value = "<request>?<message>"
```

The value is exactly the local API's request line. So turning the heat on is
the same string in both worlds:

```
setAircon?json={"ac1":{"info":{"state":"on","mode":"heat","setTemp":21}}}
```

written to

```
commands/h/<tspId>/<deviceId>/-000001757052000000
```

where `<deviceId>` is this client's Firebase auth UID (`FirebaseComms.L`).

The `%015d` zero padding matters: Firebase orders children lexicographically,
so the padding is what keeps commands in chronological order.

## Reading state

The tablet mirrors its state into `data/<...>` and the app subscribes. The
tablet also publishes its **LAN IP** to the `ip` child
(`FirebaseComms.java:1330`) — which is how a phone on the same wifi learns the
local address to switch over to. There is no LAN scan anywhere in the app.

## Connection mode

`com.air.advantage.jsondata.c.commsState` (`aircon/c.java:794-799`):

| Value | Meaning |
|---|---|
| `5` | Internet — prints `connected via: internet` |
| other | WiFi (local) |

## Pairing

The tablet's Setup → remote access screen must have **Remote Access** and
**New device pairing** enabled. Paired clients are listed there and can be
removed individually, which is also the revocation mechanism.

## Caveats

- **This depends on Advantage Air's servers.** The app is no longer supported,
  so treat the cloud path as a fallback, not a foundation.
- Rate limits are not published; be conservative with polling and prefer
  subscriptions over busy-polling.
- The credentials above are the app's *public* Firebase client config, not
  secrets — but the per-install account is, so keep it out of version control.
