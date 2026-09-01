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

# 2) Config + state dir - never overwritten, never in the repo. ONE file,
#    resolved by carwatch/config.py for every module: ~/.carwatch/config.json
#    (the systemd units point CARWATCH_STATE there). An older install that
#    kept it in /etc/carwatch/config.json is migrated, not abandoned.
STATE_DIR="$HOME_DIR/.carwatch"
CONFIG="$STATE_DIR/config.json"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
if [ ! -f "$CONFIG" ]; then
  if [ -f /etc/carwatch/config.json ]; then
    sudo cp /etc/carwatch/config.json "$CONFIG"
    sudo chown "$RUN_USER" "$CONFIG"
    echo ">> Migrated /etc/carwatch/config.json -> $CONFIG (old file left in place)"
  else
    cp "$DEST/config.example.json" "$CONFIG"
    echo ">> EDIT $CONFIG: api_key (the car's own room key), room, handle, owner (your handle), home_ssids."
  fi
fi
chmod 600 "$CONFIG"

# 2b) Dashboard token: the secret the phone dashboard needs when it reaches
#     the car through the tunnel (same-LAN requests need none). Generated
#     once; webchat.py reads ~/.carwatch/dash-token.
if [ ! -s "$STATE_DIR/dash-token" ]; then
  python3 -c 'import secrets;print(secrets.token_urlsafe(24))' > "$STATE_DIR/dash-token"
  chmod 600 "$STATE_DIR/dash-token"
  echo ">> Dashboard token generated at $STATE_DIR/dash-token (cat it when the dash asks)"
fi

# 3) Bluetooth OBD dongle MAC for the rfcomm unit. Comes from config.json's
#    "obd_mac" (or pair first with scripts/pair-bt-obd.sh, which writes it).
#    rfcomm.env stays in /etc/carwatch: that unit runs as root, not as you.
sudo mkdir -p /etc/carwatch
OBD_MAC=$(cd "$DEST" && python3 -m carwatch.config get obd_mac 2>/dev/null || true)
if [ -n "$OBD_MAC" ]; then
  printf 'OBD_MAC=%s\n' "$OBD_MAC" | sudo tee /etc/carwatch/rfcomm.env >/dev/null
else
  sudo touch /etc/carwatch/rfcomm.env
  echo ">> No obd_mac in config yet - pair the dongle later with scripts/pair-bt-obd.sh"
fi

# 4) System dependencies - everything the Python shells out to, so the
#    "zero pip dependencies" promise holds for the product, not just the code:
#    wireless-tools (iwgetid), poppler-utils (pdftotext, manual RAG), bluez +
#    bluez-alsa-utils (OBD dongle, car audio), alsa-utils (arecord/aplay for
#    the voice loop), ffmpeg (room voice notes), network-manager (nmcli wifi).
echo ">> apt: updating package lists"
sudo apt-get update -qq
sudo apt-get install -y wireless-tools poppler-utils bluez bluez-alsa-utils \
  alsa-utils ffmpeg network-manager rsync curl git >/dev/null

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

# 7) Voice (optional): the listener needs whisper.cpp for ears and piper for
#    a voice. Both guided, same as the brain; paths are the ones voice.py,
#    listen.py and voiceroom.py expect.
if [ ! -x "$HOME_DIR/carwatch-stack/whisper.cpp/build/bin/whisper-cli" ]; then
  cat <<GUIDE
>> EARS (optional, for carwatch-listen and room voice notes) - whisper.cpp once:
     mkdir -p ~/carwatch-stack && cd ~/carwatch-stack
     git clone https://github.com/ggml-org/whisper.cpp.git
     cmake -B whisper.cpp/build -S whisper.cpp && cmake --build whisper.cpp/build -j4 --target whisper-cli
     cd whisper.cpp && sh ./models/download-ggml-model.sh base      # multilingual
     sh ./models/download-ggml-model.sh base.en                      # English-only, faster
GUIDE
fi
if [ ! -x "$HOME_DIR/.local/bin/piper" ] || [ ! -f "$HOME_DIR/carwatch-stack/models/en_US-lessac-medium.onnx" ]; then
  cat <<GUIDE
>> VOICE (optional, spoken answers) - piper once:
     pip install --user piper-tts            # provides ~/.local/bin/piper
     mkdir -p ~/carwatch-stack/models && cd ~/carwatch-stack/models
     # voices from https://huggingface.co/rhasspy/piper-voices (each .onnx needs its .onnx.json):
     wget -c https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
     wget -c https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
   (Finnish: fi/fi_FI/harri/medium/fi_FI-harri-medium.onnx + .json, same place)
GUIDE
fi

cat <<DONE
>> Config: $CONFIG   (owner = your GroupMind handle, so the car answers YOU)
>> Done. Start the core (dashboard + engine watch + room agent):
     sudo systemctl enable --now carwatch-chat carwatch-obd carwatch-agent
   Optional extras when their hardware/config is ready:
     carwatch-brain (needs llama.cpp+model)  carwatch-listen (USB mic)
     carwatch-rfcomm (BT OBD dongle)         carwatch-reach (tunnel)
     carwatch-presence  carwatch-netfallback  carwatch-pairwatch
     carwatch-update.timer (hourly self-update)
DONE
