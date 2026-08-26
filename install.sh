#!/usr/bin/env bash
# CarWatch installer for Raspberry Pi OS (64-bit). Idempotent.
#
# Installs the SAME stack the reference car runs - every unit in systemd/ -
# adapted to your username. The units in the repo are verbatim what the
# reference Pi ("vadelma") runs; this script only rewrites the user and home
# path, so what you get is what we test on the real car.
set -euo pipefail

RUN_USER=${SUDO_USER:-$USER}
HOME_DIR=$(getent passwd "$RUN_USER" | cut -d: -f6)
SRC=$(cd "$(dirname "$0")" && pwd)
DEST="$HOME_DIR/CarWatch"

echo ">> Installing CarWatch for user $RUN_USER (home $HOME_DIR)"

# 1) Code lives in the user's home, exactly like on the reference car.
if [ "$SRC" != "$DEST" ]; then
  mkdir -p "$DEST"
  rsync -a --exclude .git "$SRC/" "$DEST/"
  echo ">> Code synced to $DEST"
fi

# 2) Config - never overwritten, never in the repo.
sudo mkdir -p /etc/carwatch
if [ ! -f /etc/carwatch/config.json ]; then
  sudo cp "$DEST/config.example.json" /etc/carwatch/config.json
  sudo chmod 600 /etc/carwatch/config.json
  sudo chown "$RUN_USER" /etc/carwatch/config.json
  echo ">> EDIT /etc/carwatch/config.json: your room key, room, handle, SSIDs."
fi

# 3) Bluetooth OBD dongle MAC for the rfcomm unit. Comes from config.json's
#    "obd_mac" (or pair first with scripts/pair-bt-obd.sh, which writes it).
OBD_MAC=$(python3 -c 'import json;print(json.load(open("/etc/carwatch/config.json")).get("obd_mac",""))' 2>/dev/null || true)
if [ -n "$OBD_MAC" ]; then
  printf 'OBD_MAC=%s\n' "$OBD_MAC" | sudo tee /etc/carwatch/rfcomm.env >/dev/null
else
  sudo touch /etc/carwatch/rfcomm.env
  echo ">> No obd_mac in config yet - pair the dongle later with scripts/pair-bt-obd.sh"
fi

# 4) System dependencies.
sudo apt-get install -y wireless-tools poppler-utils bluez rsync >/dev/null

# 5) Every systemd unit, user/home rewritten to yours.
for u in "$DEST"/systemd/carwatch-*.service "$DEST"/systemd/carwatch-*.timer; do
  sudo sed -e "s|/home/petrus|$HOME_DIR|g" -e "s|^User=petrus|User=$RUN_USER|" \
    "$u" | sudo tee "/etc/systemd/system/$(basename "$u")" >/dev/null
done
sudo systemctl daemon-reload
echo ">> Installed units: $(ls "$DEST"/systemd | tr '\n' ' ')"

# 6) The brain needs llama.cpp + a model. Both are guided, never silent:
#    a build takes ~20 min and the model is a 14.3 GB download - your call.
if [ ! -x "$HOME_DIR/carwatch-stack/llama.cpp/build/bin/llama-server" ]; then
  cat <<GUIDE
>> BRAIN (optional but it IS the product) - build llama.cpp once:
     sudo apt-get install -y build-essential cmake
     mkdir -p ~/carwatch-stack && cd ~/carwatch-stack
     git clone https://github.com/ggml-org/llama.cpp.git
     cmake -B llama.cpp/build -S llama.cpp && cmake --build llama.cpp/build -j4 --target llama-server
GUIDE
fi
if [ ! -f "$HOME_DIR/models/Qwen3.6-35B-A3B-UD-Q3_K_S.gguf" ]; then
  cat <<GUIDE
>> MODEL (14.3 GB, Pi 5 16 GB required for this one) - download once:
     mkdir -p ~/models && cd ~/models
     wget -c https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q3_K_S.gguf
   (a smaller Pi: pick a smaller .gguf and set it in systemd/carwatch-brain.service)
GUIDE
fi

cat <<DONE
>> Done. Start the core (dashboard + engine watch + room agent):
     sudo systemctl enable --now carwatch-chat carwatch-obd carwatch-agent
   Optional extras when their hardware/config is ready:
     carwatch-brain (needs llama.cpp+model)  carwatch-listen (USB mic)
     carwatch-rfcomm (BT OBD dongle)         carwatch-reach (tunnel)
     carwatch-presence  carwatch-netfallback  carwatch-pairwatch
     carwatch-update.timer (hourly self-update)
DONE
