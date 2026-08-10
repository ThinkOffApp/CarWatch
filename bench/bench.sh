#!/usr/bin/env bash
# CarWatch bench day: one command on a fresh Pi 5 (Raspberry Pi OS 64-bit).
# Installs the local-AI stack, downloads models, measures tokens/sec, and
# probes the WOLFBOX camera + mirror ADB. Safe to re-run; steps are skipped
# when already done.
set -euo pipefail

STACK=${CARWATCH_STACK:-$HOME/carwatch-stack}
MODELS="$STACK/models"
JOBS=$(nproc)

# Grounded Aug 9 2026: bartowski's llama.cpp-tested quants of Google's
# Gemma 4 E2B (edge, multimodal). Q4_K_M ~1.5 GB.
GEMMA_REPO="bartowski/google_gemma-4-E2B-it-GGUF"
GEMMA_FILE="google_gemma-4-E2B-it-Q4_K_M.gguf"
WHISPER_MODEL="base.en"
PIPER_VOICE="en_US-lessac-medium"

mkdir -p "$STACK" "$MODELS"

echo "== apt deps"
sudo apt-get update -qq
sudo apt-get install -y -qq git build-essential cmake curl ffmpeg \
  wireless-tools alsa-utils python3-pip adb sox libsox-fmt-all poppler-utils \
  pciutils

echo "== PCIe / NVMe link speed (SSD is the MoE bottleneck)"
# The Qwen MoE streams experts off the SSD, so NVMe bandwidth matters.
# Pi 5 defaults to PCIe Gen 2; add `dtparam=pciex1_gen=3` to
# /boot/firmware/config.txt and reboot to (roughly) double it. Gen 3 is
# technically out of spec - if dmesg shows NVMe errors, remove the line.
if command -v lspci >/dev/null 2>&1; then
  lspci -vv 2>/dev/null | grep -i -A2 -E "Non-Volatile|NVMe" \
    | grep -i "LnkSta:" | sed 's/^/  /' \
    || echo "  (no NVMe device found on PCIe - is the SSD attached?)"
  echo "  want: Speed 8GT/s (Gen3). If you see 5GT/s, Gen3 is not enabled."
else
  echo "  (install pciutils for lspci to read the link speed)"
fi
grep -q "pciex1_gen=3" /boot/firmware/config.txt 2>/dev/null \
  && echo "  config.txt: pciex1_gen=3 present" \
  || echo "  config.txt: pciex1_gen=3 NOT set (still Gen2) - add it + reboot"

echo "== llama.cpp (baseline runtime + whisper build dep)"
if [ ! -d "$STACK/llama.cpp" ]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$STACK/llama.cpp"
  cmake -S "$STACK/llama.cpp" -B "$STACK/llama.cpp/build" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$STACK/llama.cpp/build" -j "$JOBS" --target llama-server llama-bench llama-cli
fi

echo "== ik_llama.cpp (IQK-optimised, the faster Pi runtime - Potato OS)"
# Meaningfully faster than upstream on the Pi 5, and the runtime that
# makes the 30B MoE + SSD-offload path viable (petrus's Potato OS find).
if [ ! -d "$STACK/ik_llama.cpp" ]; then
  git clone --depth 1 https://github.com/ikawrakow/ik_llama.cpp "$STACK/ik_llama.cpp"
  cmake -S "$STACK/ik_llama.cpp" -B "$STACK/ik_llama.cpp/build" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$STACK/ik_llama.cpp/build" -j "$JOBS" --target llama-server llama-bench llama-cli
fi
IK_BIN="$STACK/ik_llama.cpp/build/bin"

echo "== whisper.cpp"
if [ ! -d "$STACK/whisper.cpp" ]; then
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$STACK/whisper.cpp"
  cmake -S "$STACK/whisper.cpp" -B "$STACK/whisper.cpp/build" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$STACK/whisper.cpp/build" -j "$JOBS"
  bash "$STACK/whisper.cpp/models/download-ggml-model.sh" "$WHISPER_MODEL"
fi

echo "== piper (TTS)"
if ! command -v piper >/dev/null 2>&1; then
  pip3 install --user --break-system-packages piper-tts
  python3 -m piper.download_voices "$PIPER_VOICE" --data-dir "$MODELS" || true
fi

echo "== Gemma 4 E2B GGUF"
if [ ! -f "$MODELS/$GEMMA_FILE" ]; then
  # download to tmp + mv so a killed download can never leave a truncated
  # model that the -f guard would then trust forever (kimi3 review)
  curl -L --fail -o "$MODELS/$GEMMA_FILE.part" \
    "https://huggingface.co/$GEMMA_REPO/resolve/main/$GEMMA_FILE"
  mv "$MODELS/$GEMMA_FILE.part" "$MODELS/$GEMMA_FILE"
fi

echo "== Qwen3-30B-A3B MoE (SSD-offload path, needs the SSD mounted)"
# The smart big-model path: Qwen3.6-35B-A3B MoE (Apr 2026, 35B
# total / 3B active, Apache 2.0) - the Potato OS 30B recipe applied to
# the newer, stronger model petrus flagged. ~8-9 tok/s class on a Pi 5
# when experts stream off a fast SSD; faster with 16GB. Set
# CARWATCH_SSD to a mounted SSD dir to enable; otherwise skipped (10GB
# does not fit in 8GB RAM alone).
QWEN_REPO="unsloth/Qwen3.6-35B-A3B-GGUF"
QWEN_FILE="Qwen3.6-35B-A3B-UD-Q3_K_S.gguf"
SSD="${CARWATCH_SSD:-}"
if [ -n "$SSD" ] && [ -d "$SSD" ]; then
  if [ ! -f "$SSD/$QWEN_FILE" ]; then
    echo "  downloading $QWEN_FILE (~10GB) to the SSD..."
    curl -L --fail -o "$SSD/$QWEN_FILE.part" \
      "https://huggingface.co/$QWEN_REPO/resolve/main/$QWEN_FILE" && \
      mv "$SSD/$QWEN_FILE.part" "$SSD/$QWEN_FILE"
  fi
else
  echo "  (skipped: set CARWATCH_SSD=/path/to/ssd to bench the 30B MoE)"
fi

echo "== benchmark: Gemma 4 E2B on both runtimes (tokens/sec)"
echo "-- upstream llama.cpp:"
"$STACK/llama.cpp/build/bin/llama-bench" -m "$MODELS/$GEMMA_FILE" -t "$JOBS" || true
echo "-- ik_llama.cpp:"
"$IK_BIN/llama-bench" -m "$MODELS/$GEMMA_FILE" -t "$JOBS" || true
if [ -n "$SSD" ] && [ -f "$SSD/$QWEN_FILE" ]; then
  echo "-- ik_llama.cpp, Qwen3-30B MoE (SSD offload):"
  "$IK_BIN/llama-bench" -m "$SSD/$QWEN_FILE" -t "$JOBS" -ngl 0 || true
fi

echo "== thermal check"
vcgencmd measure_temp || true

echo
echo "== WOLFBOX probe (connect the Pi to the camera's wifi AP first)"
python3 -m carwatch.wolfbox --probe || true

echo
echo "== mirror ADB probe (G900 runs a basic Android; try its AP gateway)"
for host in 192.168.1.254 192.168.42.1 193.168.0.1; do
  timeout 6 adb connect "$host:5555" 2>/dev/null || true
done
adb devices -l

echo
echo "Bench day complete. Next:"
echo "  1. paste llama-bench + probe output into a CarWatch issue"
echo "  2. wire wolfbox.py constants from the probe results"
echo "  3. start the voice loop: python3 -m carwatch.voice --once"
