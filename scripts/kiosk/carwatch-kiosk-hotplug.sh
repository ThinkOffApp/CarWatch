#!/bin/bash
# Called by udev (90-carwatch-kiosk.rules) on every DRM hotplug event, with the
# kiosk user as $1. Asks systemd to start the kiosk unit and returns at once;
# the launcher itself decides whether a panel is connected (exit 78 if not).
# A plain RUN is used instead of SYSTEMD_WANTS because Wants on a device unit
# fires only when the device becomes active, and card0 stays active across
# panel unplug/replug (codexmb, CarWatch #60 review).
UNIT="carwatch-kiosk@${1:?kiosk user}.service"
# A unit left 'failed' (start-limit-hit, a crashed browser) would refuse the
# start; clear that first, then ask for the start without waiting.
/usr/bin/systemctl reset-failed "$UNIT" 2>/dev/null || true
exec /usr/bin/systemctl start --no-block "$UNIT"
