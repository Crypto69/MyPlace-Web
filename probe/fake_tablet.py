"""A stand-in for the MyPlace wall tablet, for testing without the real one.

Mimics the protocol documented in docs/API.md: GET /getSystemData and
GET /setAircon?json=<blob>, shaped like a MyAir5 with three zones.

    python3 probe/fake_tablet.py 2025
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

STATE = {
    "aircons": {
        "ac1": {
            "info": {
                "name": "Ducted Heating",
                "state": "off",
                "mode": "heat",
                "setTemp": 20.0,
                "fan": "auto",
                "myZone": 1,
                "noOfZones": 3,
                "airconErrorCode": 0,
                "filterCleanStatus": 0,
                "countDownToOff": 0,
                "countDownToOn": 0,
            },
            "zones": {
                # type > 0 => has a temperature sensor (controlled by setTemp)
                "z01": {"name": "Living Room", "number": 1, "state": "open",
                        "value": 100, "setTemp": 21.0, "measuredTemp": 19.5, "type": 1},
                "z02": {"name": "Bedroom", "number": 2, "state": "close",
                        "value": 0, "setTemp": 20.0, "measuredTemp": 18.0, "type": 1},
                # type 0 => damper-only (controlled by value %)
                "z03": {"name": "Study", "number": 3, "state": "open",
                        "value": 50, "setTemp": None, "measuredTemp": None, "type": 0},
            },
        }
    },
    "system": {"name": "My Place", "needsUpdate": False},
}


def deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        endpoint = parsed.path.lstrip("/")

        if endpoint == "getSystemData":
            return self._send(STATE)

        if endpoint == "setAircon":
            raw = parse_qs(parsed.query).get("json", ["{}"])[0]
            try:
                change = json.loads(raw)
            except json.JSONDecodeError:
                return self._send({"ack": False, "reason": "bad json"}, 400)
            print(f"  setAircon <- {json.dumps(change)}")
            deep_merge(STATE["aircons"], change)
            return self._send({"ack": True, "request": "setAircon"})

        return self._send({"ack": False, "reason": "unknown endpoint"}, 404)

    def log_message(self, fmt, *args):
        print("  " + fmt % args)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
    print(f"fake MyPlace tablet listening on http://127.0.0.1:{port}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
