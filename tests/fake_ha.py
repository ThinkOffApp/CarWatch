#!/usr/bin/env python3
"""Fake Home Assistant for bench-testing carwatch.mercedesme at home.

Serves the two endpoints the provider touches (GET /api/, GET /api/states)
with mbapi2020-shaped entities for two cars - one friendly-named, one
VIN-named like a freshly added car - so the suffix mapper and the label
logic are both exercised. Auth: any Bearer token equal to TOKEN passes,
everything else gets HA's real 401 shape.

    python3 tests/fake_ha.py 18123
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "bench-token-for-fake-ha-only-not-secret"
ATTR = "Data provided by Mercedes-Benz"


def _e(eid, state, friendly, extra=None):
    # mbapi2020 sets NO attribution attribute (verified against its source,
    # Aug 26 2026) - the fake must not be friendlier than reality.
    a = {"friendly_name": friendly}
    a.update(extra or {})
    return {"entity_id": eid, "state": state, "attributes": a}


STATES = [
    # E 300 e - friendly-named, granular doors/windows
    _e("lock.e300e_lock", "locked", "E 300 e Lock"),
    _e("sensor.e300e_state_of_charge", "76.5", "E 300 e State of charge", {"unit_of_measurement": "%"}),
    _e("sensor.e300e_range_electric", "41", "E 300 e Range electric", {"unit_of_measurement": "km"}),
    _e("binary_sensor.e300e_charging_active", "off", "E 300 e Charging active"),
    _e("sensor.e300e_tirepressure_front_left", "250", "E 300 e Tire pressure front left", {"unit_of_measurement": "kPa"}),
    _e("sensor.e300e_tirepressure_front_right", "250", "E 300 e Tire pressure front right", {"unit_of_measurement": "kPa"}),
    _e("sensor.e300e_tirepressure_rear_left", "260", "E 300 e Tire pressure rear left", {"unit_of_measurement": "kPa"}),
    _e("sensor.e300e_tirepressure_rear_right", "255", "E 300 e Tire pressure rear right", {"unit_of_measurement": "kPa"}),
    _e("binary_sensor.e300e_window_front_left", "off", "E 300 e Window front left"),
    _e("binary_sensor.e300e_window_front_right", "off", "E 300 e Window front right"),
    _e("binary_sensor.e300e_door_front_left", "off", "E 300 e Door front left"),
    _e("sensor.e300e_odometer", "48211", "E 300 e Odometer", {"unit_of_measurement": "km"}),
    _e("sensor.e300e_sunroof", "closed", "E 300 e Sunroof"),
    _e("sensor.e300e_tank_level", "58", "E 300 e Tank level", {"unit_of_measurement": "%"}),
    # GLE - VIN-named like a car before its friendly rename, aggregate closures
    _e("lock.wdc292xxfake_lock", "locked", "GLE Lock"),
    _e("binary_sensor.wdc292xxfake_windows_closed", "on", "GLE Windows closed"),
    _e("binary_sensor.wdc292xxfake_doors_closed", "on", "GLE Doors closed"),
    _e("sensor.wdc292xxfake_adblue_level", "78", "GLE AdBlue level", {"unit_of_measurement": "%"}),
    _e("sensor.wdc292xxfake_range_liquid", "540", "GLE Range liquid", {"unit_of_measurement": "km"}),
    _e("sensor.wdc292xxfake_ignition_state", "lock", "GLE Ignition state"),
    _e("sensor.wdc292xxfake_odometer", "91002", "GLE Odometer", {"unit_of_measurement": "km"}),
    # noise that must be ignored: a lone suffix hit from another integration
    _e("lock.front_door_lock", "locked", "Front door"),
    # noise that must be ignored (non-vehicle entities)
    _e("sensor.random_kitchen_temp", "21.5", "Kitchen temp", {"attribution": "someone else"}),
    {"entity_id": "sun.sun", "state": "below_horizon", "attributes": {}},
    # unavailable value that must be dropped, not shown as a fact
    _e("sensor.e300e_charging_power", "unavailable", "E 300 e Charging power"),
]


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            body = b'{"message": "Unauthorized"}'
            self.send_response(401)
        elif self.path == "/api/":
            body = b'{"message": "API running."}'
            self.send_response(200)
        elif self.path == "/api/states":
            body = json.dumps(STATES).encode()
            self.send_response(200)
        else:
            body = b'{"message": "not found"}'
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18123
    print(f"fake HA on :{port}  token={TOKEN}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
