#!/bin/bash
# If the Pi has no network for a while, become one (fallback access point).
# In the car with no phone hotspot this is the only way to reach it.
# Create the AP profile once, with your own SSID/password (stored by
# NetworkManager, never in this repo):
#   nmcli device wifi hotspot ifname wlan0 con-name carwatch-ap \
#         ssid CarWatch password <yours>
#   nmcli connection modify carwatch-ap connection.autoconnect no
AP_CON=${CARWATCH_AP_CON:-carwatch-ap}
sleep 150   # give boot + known wifi a fair chance first
while true; do
  if nmcli -t -f DEVICE,STATE dev status | grep -qE "^(wlan0|eth0):connected"; then
    :   # online normally; if our AP is somehow up alongside, leave things be
  else
    if ! nmcli -t -f NAME connection show --active | grep -q "^${AP_CON}$"; then
      logger -t net-fallback "no network for 150s+, starting fallback AP"
      nmcli connection up "$AP_CON"
    fi
  fi
  sleep 180
done
