#!/usr/bin/env bash
# CarWatch board-vs-board LLM benchmark.
#
# Re-runs the README's reference table (llama-bench pp512 / tg128, tokens per
# second) on whatever board this runs on, the same way on every board, so two
# boards can be compared like for like:
#   - the same llama.cpp commit (pinned below, LLAMA_CPP_REF overrides; built
#     in its own dir, never in the brain's build),
#   - the same model files and quantizations (MODEL_SPECS below),
#   - CPU only in the main table, at 4 threads (the README baseline) AND at
#     nproc threads, so a core-count advantage shows up as its own row,
#   - llama-bench -p 512 -n 128 -r 3 -o json: mean and stddev over 3 reps,
#   - a cold-load wall time per model (page cache dropped when sudo -n works),
#   - a device identity block (board, CPU, RAM, kernel, storage, temps,
#     throttling, governor) recorded next to the numbers.
# GPU / NPU runs are optional (--accel) and land in a separate, labelled
# ACCELERATED section; they never touch the CPU columns.
#
# Optional: --quality (grounded-answer checks, bench/quality.py) and
# --update-registry (merge into the dash's ~/.config/carwatch/model-bench.json).
#
# Car safety: CarWatch services are never stopped unless --stop-brain or
# --mode clean|both is given; whatever was stopped is restarted on exit (also
# on Ctrl-C or failure) and verified. Without --stop-brain the script refuses
# to run while a llama-server holds more than 2 GB of RAM, because the brain
# would be competing for the same cores and memory and every number would be
# wrong.
#
# Usage (see --help):
#   bench/board-bench.sh --mode both --quality --update-registry   # the Pi
#   bench/board-bench.sh --mode clean --quality                    # other boards
#   python3 bench/compare.py results/<pi>.json results/<other>.json
set -euo pipefail

# ------------------------------------------------------------------ config
# One line per model, '|'-separated:
#   id | label | filename globs (case-insensitive, ';'-separated, first match
#   wins) | HF repo | HF file (for --download; empty = no known source) | note
# The note is printed in the tables next to every number for that model.
# Sources checked against the HuggingFace API on 2 Oct 2026 ("always run
# latest": Gemma 4 and Ornith 1.5 are the newest generations).
MODEL_SPECS=(
  "gemma4-e2b|Gemma 4 E2B Q4_K_M|google_gemma-4-E2B-it-Q4_K_M.gguf;*gemma-4-E2B*Q4_K_M*.gguf|bartowski/google_gemma-4-E2B-it-GGUF|google_gemma-4-E2B-it-Q4_K_M.gguf|"
  # The E4B QAT repo also ships a projector; on 28 Aug a download grabbed a
  # 376 MB projector as "the model". Exact name first, MIN_MODEL_BYTES below.
  "gemma4-e4b|Gemma 4 E4B QAT Q4_0|gemma-4-E4B_q4_0-it.gguf;gemma-4-E4B-qat-q4_0.gguf;*gemma-4-E4B*it*Q4_0*.gguf|google/gemma-4-E4B-it-qat-q4_0-gguf|gemma-4-E4B_q4_0-it.gguf|"
  "ornith-9b|Ornith 1.5 9B (dense) Q4_K_M|Ornith-1.5-9B-Q4_K_M.gguf;*Ornith*1.5*9B*Q4_K_M*.gguf|ornith-ai/Ornith-1.5-9B-GGUF|Ornith-1.5-9B-Q4_K_M.gguf|"
  # REPLACES the README's "Qwen3.6 27B dense IQ2_M" (10.8 GB) row: Qwen3.8
  # 27B is the newer generation, Q2_K_XL (9.83 GB) the nearest size and a
  # K-quant, which is lighter on Arm CPUs than IQ2. Not the same row.
  "qwen38-27b|Qwen3.8 27B dense UD-Q2_K_XL|Qwen3.8-27B-UD-Q2_K_XL.gguf;*Qwen3.8-27B*Q2_K_XL*.gguf|unsloth/Qwen3.8-27B-GGUF|Qwen3.8-27B-UD-Q2_K_XL.gguf|replaces README Qwen3.6 27B IQ2_M: newer generation, different quant"
  # The Pi's 13.7 GB IQ3_XXS file has no recorded source (bartowski's
  # IQ3_XXS is 15.34 GB, a different file), so no download is configured:
  # fill the repo/file in once the Pi's file is identified.
  "ornith-35b|Ornith 1.5 35B MoE IQ3_XXS|*Ornith*1.5*35B*IQ3_XXS*.gguf||"
  # The car's CURRENT brain on vadelma (~/.config/carwatch/brain.env, 2 Oct
  # 2026). Two repos publish this exact filename (inclusionAI 4.82 GB,
  # bartowski 4.92 GB); the size is recorded per run, so check which one a
  # board has before comparing. --download fetches the official one.
  "ling3-tiny|Ling 3.0 tiny Q4_K_M (current car brain)|Ling-3.0-tiny-Q4_K_M.gguf;*Ling-3.0-tiny*Q4_K_M*.gguf|inclusionAI/Ling-3.0-tiny-GGUF|Ling-3.0-tiny-Q4_K_M.gguf|"
  # KEPT on Qwen3.6: no Qwen3.8 model fits 16 GB except the dense 27B.
  "qwen36-35b|Qwen3.6 35B MoE Q3_K_S|Qwen3.6-35B-A3B-UD-Q3_K_S.gguf;*Qwen3.6-35B*Q3_K_S*.gguf|unsloth/Qwen3.6-35B-A3B-GGUF|Qwen3.6-35B-A3B-UD-Q3_K_S.gguf|kept: no Qwen3.8 model fits 16 GB except the dense 27B (Qwen3.8 = 27B dense, Flash-Next 180B, 2.4T-A95B)"
)
# Smaller .gguf matches are projectors / drafts, never one of these models.
MIN_MODEL_BYTES=${BOARD_BENCH_MIN_BYTES:-1000000000}
N_PROMPT=512
N_GEN=128
REPS=3
BASE_THREADS=4
HEADROOM_GB=1.5            # NOFIT if file size + this > MemAvailable
BRAIN_RSS_LIMIT_KB=$((2 * 1024 * 1024))
BRAIN_UNIT=carwatch-brain
BRAIN_HEALTH=http://127.0.0.1:8081/health
# llama.cpp v0.5.0 (tag, 23 Sep 2026), pinned by full sha so every board
# builds the identical commit. Override with LLAMA_CPP_REF; --llama-bin
# benches an existing build instead (its commit is recorded either way).
DEFAULT_LLAMA_CPP_REF=7fe450e19305b828c199d602c23a8337aaa1f03b
# --mode clean stops these (plus the brain) and restarts exactly the ones
# that were active. reach / netfallback / rfcomm are connectivity and stay
# up: stopping them could cut the remote path to the car mid-run.
CLEAN_UNITS=(carwatch-agent carwatch-chat carwatch-listen carwatch-obd
             carwatch-presence carwatch-pairwatch carwatch-update.timer
             "carwatch-kiosk@$(id -un).service")
# The dash kiosk (Chromium under cage) burns about one core: 2 Oct 2026 a Pi 5 run with it up measured Gemma 4 E2B
# tg 2.15 tok/s vs 6.96 with it stopped, since -t 4 on four cores waits on the busiest one.
BRAIN_ENV=${CARWATCH_BRAIN_ENV:-$HOME/.config/carwatch/brain.env}
QUALITY_PORT_AVOID=8081    # carwatch-brain's port
STACK=${CARWATCH_STACK:-$HOME/carwatch-stack}
BENCH_HOME=${CARWATCH_BENCH_HOME:-$STACK/bench}
LLAMA_REPO=https://github.com/ggml-org/llama.cpp
IK_REPO=https://github.com/ikawrakow/ik_llama.cpp
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
COMPARE="$HERE/compare.py"

# ------------------------------------------------------------------ args
MODEL_DIRS=()
ONLY=""
STOP_BRAIN=0
WITH_IK=0
ACCEL=""
OUT_DIR="$HERE/results"
DRY_RUN=0
DOWNLOAD=0
MODE=carwatch
LLAMA_BIN=""
QUALITY=0
UPDATE_REGISTRY=0

usage() {
  cat <<EOF
usage: $0 [options]

  --models DIR      model directory (repeatable). Default: ~/models and
                    ~/carwatch-stack/models, the dirs the dash scans.
  --download        fetch MISSING models from their HF repo into the first
                    model dir (curl to .part, then mv), before anything stops.
  --only IDS        comma-separated subset of: $(for s in "${MODEL_SPECS[@]}"; do printf '%s ' "${s%%|*}"; done)
  --mode M          carwatch (default): CarWatch services keep running, as
                    in the car (the brain itself still has to be stopped with
                    --stop-brain if it is resident). clean: stop them all incl.
                    the brain, restart and verify after. both: carwatch pass,
                    then clean pass. clean/both imply --stop-brain and need
                    passwordless sudo. On a board without CarWatch the two
                    modes are the same; compare boards on "clean".
  --stop-brain      stop $BRAIN_UNIT for the run, restart it after and wait
                    for $BRAIN_HEALTH (needs passwordless sudo).
  --llama-bin DIR   bench an existing llama.cpp build's bin dir instead of
                    the pinned build (commit recorded; boards may then differ).
  --quality         also run the grounded-answer checks (bench/quality.py)
                    per model through a llama-server on a free local port.
  --update-registry merge the numbers into ~/.config/carwatch/model-bench.json
                    (the dash's per-device table), at the brain's threads.
  --ik              also bench ik_llama.cpp (IK_LLAMA_REF pins it), recorded
                    as a separate runtime.
  --accel KIND      also run an ACCELERATED pass: vulkan or opencl (Adreno).
                    Builds a separate llama.cpp; reported in its own table.
  --headroom-gb N   NOFIT margin over the file size (default $HEADROOM_GB).
  --out DIR         results directory (default bench/results).
  --dry-run         print identity + which models would run / skip, no builds.

env:
  LLAMA_CPP_REF     llama.cpp commit (full 40-char sha) or tag. Default
                    $DEFAULT_LLAMA_CPP_REF (v0.5.0).
                    Use the SAME value on every board. Built in
                    \$CARWATCH_BENCH_HOME/llama.cpp-<ref> (default
                    $BENCH_HOME), never in the brain's own build.
  IK_LLAMA_REF      ik_llama.cpp ref for --ik (default: its main HEAD, recorded).
  NPU_RUNNER        optional executable hook, see npu_hook() in this script.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --models) MODEL_DIRS+=("$2"); shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    --stop-brain) STOP_BRAIN=1; shift ;;
    --ik) WITH_IK=1; shift ;;
    --accel) ACCEL="$2"; shift 2 ;;
    --headroom-gb) HEADROOM_GB="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --download) DOWNLOAD=1; shift ;;
    --mode) MODE="$2"; shift 2 ;;
    --llama-bin) LLAMA_BIN="$2"; shift 2 ;;
    --quality) QUALITY=1; shift ;;
    --update-registry) UPDATE_REGISTRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
if [ ${#MODEL_DIRS[@]} -eq 0 ]; then
  MODEL_DIRS=("$HOME/models" "$HOME/carwatch-stack/models")
fi
case "$ACCEL" in ""|vulkan|opencl) ;; *) echo "--accel must be vulkan or opencl" >&2; exit 2 ;; esac
case "$MODE" in
  carwatch) MODES=(carwatch) ;;
  clean) MODES=(clean); STOP_BRAIN=1 ;;
  both) MODES=(carwatch clean); STOP_BRAIN=1 ;;
  *) echo "--mode must be carwatch, clean or both" >&2; exit 2 ;;
esac
# Test hooks (tests/test_bench_compare.py drives the script with stubs).
MEMINFO=${BOARD_BENCH_MEMINFO:-/proc/meminfo}
if [ "$(uname -s)" != "Linux" ] && [ "${BOARD_BENCH_ALLOW_NON_LINUX:-0}" != 1 ]; then
  echo "board-bench.sh runs on the board itself (Linux); this is $(uname -s)." >&2
  exit 2
fi
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }

NPROC=$(nproc)
STAMP=$(date -u +%Y-%m-%d)
HOST=$(hostname)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/board-bench.XXXXXX")
META="$WORK/meta.jsonl"
: > "$META"
mkdir -p "$OUT_DIR"
OUT_JSON="$OUT_DIR/$HOST-$STAMP.json"
OUT_MD="$OUT_DIR/$HOST-$STAMP.md"
if [ -e "$OUT_JSON" ]; then
  OUT_JSON="$OUT_DIR/$HOST-$(date -u +%Y-%m-%dT%H%M%SZ).json"
  OUT_MD="${OUT_JSON%.json}.md"
fi

log() { printf '== %s\n' "$*" >&2; }
rec() { python3 "$COMPARE" record "$META" "$@"; }

# ------------------------------------------------------------------ identity
cat_glob() { [ $# -gt 0 ] && [ -e "$1" ] && cat "$@" 2>/dev/null; return 0; }

board_model() {
  if [ -r /proc/device-tree/model ]; then
    tr -d '\0' < /proc/device-tree/model
  elif [ -r /sys/class/dmi/id/product_name ]; then
    printf '%s %s' "$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || true)" \
      "$(cat /sys/class/dmi/id/product_name)"
  else
    echo unknown
  fi
}

cpu_models_json() {
  # big.LITTLE boards list one "Model name" per cluster; keep them all.
  lscpu 2>/dev/null | sed -n 's/^ *Model name: *//p' | sort -u \
    | python3 -c 'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))'
}

max_mhz() {
  local m
  m=$(lscpu 2>/dev/null | sed -n 's/^ *CPU max MHz: *//p' | sort -n | tail -1)
  if [ -z "$m" ]; then
    # cat_glob, never a bare cat: under nullglob an unmatched glob would
    # leave cat reading stdin forever
    m=$(cat_glob /sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq \
      | sort -n | tail -1 | awk '{ if ($1) printf "%.0f", $1 / 1000 }')
  fi
  printf '%s' "${m:-unknown}"
}

meminfo_kb() { awk -v k="$1:" '$1 == k { print $2 }' "$MEMINFO"; }

governor() {
  cat_glob /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor \
    | sort | uniq -c | awk '{ printf "%s%s x%s", (NR > 1 ? ", " : ""), $2, $1 }'
}

storage_of() {
  local dir=$1 src fs pk info
  src=$(findmnt -no SOURCE -T "$dir" 2>/dev/null || df -P "$dir" | awk 'NR == 2 { print $1 }')
  fs=$(findmnt -no FSTYPE -T "$dir" 2>/dev/null || echo "?")
  pk=$(lsblk -no PKNAME "$src" 2>/dev/null | head -1 || true)
  info=""
  if [ -n "$pk" ]; then
    info=$(lsblk -dno TRAN,MODEL,SIZE "/dev/$pk" 2>/dev/null | xargs || true)
  fi
  printf '%s (%s%s%s)' "$src" "$fs" "${pk:+, /dev/$pk}" "${info:+ $info}"
}

temp_c() {
  if command -v vcgencmd >/dev/null 2>&1; then
    vcgencmd measure_temp 2>/dev/null | sed -n "s/^temp=\([0-9.]*\).*/\1/p" && return 0
  fi
  local z best=""
  for z in /sys/class/thermal/thermal_zone*/temp; do
    [ -r "$z" ] || continue
    best=$(awk -v cur="$best" '{ t = $1 / 1000; if (cur == "" || t > cur) print t; else print cur }' "$z")
  done
  printf '%s' "${best:-unknown}"
}

thermal_zones() {
  local z out=""
  for z in /sys/class/thermal/thermal_zone*; do
    [ -r "$z/temp" ] || continue
    out+="$(cat "$z/type" 2>/dev/null || basename "$z")=$(awk '{ printf "%.1f", $1 / 1000 }' "$z/temp") "
  done
  printf '%s' "${out% }"
}

throttled() {
  if command -v vcgencmd >/dev/null 2>&1; then
    vcgencmd get_throttled 2>/dev/null | sed 's/^throttled=//'
  else
    printf 'n/a (no vcgencmd; see thermal zones)'
  fi
}

record_identity() {
  local os
  os=$(. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-unknown}") || os=unknown
  rec section=device \
    hostname="$HOST" \
    model="$(board_model)" \
    cpu_models:="$(cpu_models_json)" \
    cores:="$NPROC" \
    max_mhz="$(max_mhz)" \
    mem_total_bytes:="$(( $(meminfo_kb MemTotal) * 1024 ))" \
    mem_available_start_bytes:="$(( $(meminfo_kb MemAvailable) * 1024 ))" \
    kernel="$(uname -r)" \
    arch="$(uname -m)" \
    os="$os" \
    governor="$(governor)" \
    storage="$(storage_of "${FOUND_DIR:-${MODEL_DIRS[0]}}")" \
    temp_start_c="$(temp_c)" \
    thermal_zones_start="$(thermal_zones)" \
    throttled_start="$(throttled)"
}

# ------------------------------------------------------------------ builds
ref_dir_name() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'; }

# ARM CPUs whose kernel reports dot product (asimddp) but whose build left it out: GCC's -mcpu=native resolved to a
# target without it on the Ventuno Q (Qualcomm A78C + A55, GCC 13), and prompt speed fell 2.3x with no error anywhere.
# Prints the explicit -march to use when that happened, nothing otherwise.
arm_dotprod_missing() {
  local cache=$1/build/CMakeCache.txt feats
  [ "$(uname -m)" = aarch64 ] && [ -r "$cache" ] || return 0
  feats=$(grep -m1 '^Features' /proc/cpuinfo 2>/dev/null)
  case " $feats " in *" asimddp "*) ;; *) return 0 ;; esac
  grep -q '^HAVE_DOTPROD:INTERNAL=1' "$cache" && return 0
  case " $feats " in *" asimdhp "*) echo "armv8.2-a+dotprod+fp16" ;; *) echo "armv8.2-a+dotprod" ;; esac
}

# build_llama NAME REPO REF DIR TARGETS... ; extra cmake flags in CMAKE_EXTRA
build_llama() {
  local name=$1 repo=$2 ref=$3 dir=$4; shift 4
  local targets=("$@") t missing=0
  for t in "${targets[@]}"; do
    [ -x "$dir/build/bin/$t" ] || missing=1
  done
  if [ $missing -eq 0 ] && [ -n "$(arm_dotprod_missing "$dir")" ]; then
    log "WARNING: $name: existing build lacks ARM dot product although this CPU has it; rebuilding"
    missing=1
  fi
  if [ $missing -eq 0 ]; then
    log "$name: reusing build in $dir"
    return 0
  fi
  for t in git cmake make c++; do
    command -v "$t" >/dev/null || { echo "missing $t: sudo apt-get install -y git cmake build-essential" >&2; return 1; }
  done
  if [ ! -d "$dir/.git" ]; then
    log "$name: fetching $ref into $dir"
    mkdir -p "$dir"
    git -C "$dir" init -q
    git -C "$dir" fetch -q --depth 1 "$repo" "$ref"
    git -C "$dir" checkout -q --detach FETCH_HEAD
  fi
  log "$name: building ${targets[*]} (-j $NPROC)"
  # shellcheck disable=SC2086  # CMAKE_EXTRA is a deliberate word list
  cmake -S "$dir" -B "$dir/build" -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF \
    ${CMAKE_EXTRA:-} > "$dir/cmake.log" 2>&1 || { tail -20 "$dir/cmake.log" >&2; return 1; }
  local arch
  arch=$(arm_dotprod_missing "$dir")
  if [ -n "$arch" ]; then
    log "WARNING: $name: native CPU detection left out ARM dot product; configuring with -march=$arch instead"
    # shellcheck disable=SC2086
    cmake -S "$dir" -B "$dir/build" -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF -DGGML_NATIVE=OFF \
      -DGGML_CPU_ARM_ARCH="$arch" ${CMAKE_EXTRA:-} >> "$dir/cmake.log" 2>&1 || { tail -20 "$dir/cmake.log" >&2; return 1; }
    [ -z "$(arm_dotprod_missing "$dir")" ] || { echo "dot product still missing after -march=$arch" >&2; return 1; }
  fi
  local built=0
  for t in "${targets[@]}"; do
    # llama-simple / llama-cli are optional; llama-bench is the one we need
    if cmake --build "$dir/build" -j "$NPROC" --target "$t" >> "$dir/build.log" 2>&1; then
      built=1
    else
      log "$name: target $t did not build (see $dir/build.log)"
    fi
  done
  [ $built -eq 1 ] && [ -x "$dir/build/bin/llama-bench" ]
}

backends_of() {
  local cache=$1/build/CMakeCache.txt
  if [ -r "$cache" ]; then
    grep -E '^GGML_(NATIVE|CPU|VULKAN|OPENCL|OPENCL_USE_ADRENO_KERNELS|BLAS|KLEIDIAI|CUDA|METAL)(:[A-Z]+)?=' "$cache" \
      | sed 's/:[A-Z]*=/=/' | tr '\n' ' ' | sed 's/ $//' || true
  else
    printf 'unknown (no CMakeCache.txt)'
  fi
}

setup_runtime() {   # sets BIN (the llama.cpp bin dir) and LLAMA_DIR
  local ref commit
  if [ -n "$LLAMA_BIN" ]; then
    BIN=$(cd "$LLAMA_BIN" && pwd)
    LLAMA_DIR=$(cd "$BIN/../.." && pwd)
    [ -x "$BIN/llama-bench" ] || { echo "--llama-bin $LLAMA_BIN has no llama-bench" >&2; exit 2; }
    ref="existing build (--llama-bin)"
    log "using the existing build in $BIN (boards may then run different commits)"
  else
    ref=${LLAMA_CPP_REF:-$DEFAULT_LLAMA_CPP_REF}
    LLAMA_DIR="$BENCH_HOME/llama.cpp-$(ref_dir_name "$ref")"
    CMAKE_EXTRA="" build_llama llama.cpp "$LLAMA_REPO" "$ref" "$LLAMA_DIR" \
      llama-bench llama-simple llama-cli llama-server || { echo "llama.cpp build failed" >&2; exit 1; }
    BIN="$LLAMA_DIR/build/bin"
  fi
  commit=$(git -C "$BIN" rev-parse HEAD 2>/dev/null || echo unknown)
  rec section=runtime name=llama.cpp ref="$ref" commit="$commit" bin="$BIN" \
    cmake_flags="$(backends_of "$LLAMA_DIR")"
}

# ------------------------------------------------------------------ services
# --mode clean: everything CarWatch runs that costs CPU or RAM stops for the
# pass; exactly the units that were active come back, and each is verified.
STOPPED_UNITS=""
stop_clean_units() {
  local u
  sudo -n true 2>/dev/null || { echo "--mode clean needs passwordless sudo (sudo -n) to stop/start CarWatch services" >&2; exit 2; }
  for u in "${CLEAN_UNITS[@]}"; do
    if systemctl is-active -q "$u" 2>/dev/null; then
      log "stopping $u (clean pass; restarted on exit)"
      sudo -n systemctl stop "$u" && STOPPED_UNITS+="$u "
    fi
  done
  rec section=services clean_stopped="${STOPPED_UNITS% }"
}

# Anything else eating CPU at the start skews every number: record it, and say so loudly. ps's %CPU is each
# process's average over its lifetime, so this catches long-running hogs (a kiosk, a desktop app), not bursts.
check_busy_others() {
  local busy
  # Current load, not ps's lifetime %CPU (a ps that just started reports itself at 200-700%): sample per-process CPU
  # ticks from /proc twice, one second apart, and report anything above 10% of one core, excluding this sampler.
  busy=$(python3 - "$$" <<'PYEOF' 2>/dev/null
import os, sys, time
skip = {int(sys.argv[1]), os.getpid(), os.getppid()}
def ticks():
    t = {}
    for p in os.listdir("/proc"):
        if not p.isdigit() or int(p) in skip: continue
        try:
            raw = open(f"/proc/{p}/stat").read()
            name = raw[raw.index("(") + 1:raw.rindex(")")]
            f = raw[raw.rindex(")") + 2:].split()
            t[int(p)] = (name, int(f[11]) + int(f[12]))
        except (OSError, ValueError, IndexError):
            pass
    return t
hz = os.sysconf("SC_CLK_TCK"); a = ticks(); time.sleep(1.0); b = ticks()
use = sorted(((b[p][1] - a[p][1]) * 100 // hz, b[p][0]) for p in b if p in a)
print(" ".join(f"{n}({c}%)" for c, n in reversed(use) if c > 10)[:400])
PYEOF
)
  rec section=services busy_at_start="${busy% }"
  [ -n "$busy" ] && log "WARNING: other processes busy at start, numbers may be low: ${busy% }"
  return 0
}

restore_units() {
  [ -n "$STOPPED_UNITS" ] || return 0
  local u bad="" units=$STOPPED_UNITS
  STOPPED_UNITS=""
  for u in $units; do
    sudo -n systemctl start "$u" || true
  done
  sleep 3
  for u in $units; do
    systemctl is-active -q "$u" 2>/dev/null || bad+="$u "
  done
  rec section=services restored="${units% }" not_active_after_restore="${bad% }"
  if [ -n "$bad" ]; then
    echo "WARNING: not active after restore: $bad. Check: systemctl status $bad" >&2
  else
    log "restored: $units"
  fi
}

# ------------------------------------------------------------------ brain
llama_server_max_rss_kb() {
  { ps -C llama-server -o rss= 2>/dev/null || true; } | sort -n | tail -1 | tr -d ' '
}

BRAIN_STOPPED=0
restore_brain() {
  [ "$BRAIN_STOPPED" -eq 1 ] || return 0
  BRAIN_STOPPED=0
  log "restarting $BRAIN_UNIT"
  local ok=false health="no answer" i
  if sudo -n systemctl start "$BRAIN_UNIT"; then
    # a 15 GB model off microSD takes minutes; the unit allows 600 s
    for i in $(seq 1 120); do
      if curl -fsS -m 3 "$BRAIN_HEALTH" >/dev/null 2>&1; then
        ok=true; health="healthy after $((i * 5))s"; break
      fi
      sleep 5
    done
  else
    health="sudo systemctl start failed"
  fi
  rec section=brain restored:="$ok" health="$health" \
    active="$(systemctl is-active "$BRAIN_UNIT" 2>/dev/null || true)"
  if [ "$ok" = true ]; then
    log "$BRAIN_UNIT is back ($health)"
  else
    echo "WARNING: $BRAIN_UNIT did not come back ($health). Check: systemctl status $BRAIN_UNIT" >&2
  fi
}

finish() {
  local rc=$?
  stop_quality_server || true
  restore_units || true
  restore_brain || true
  # only a run that benched something leaves a result file; a refusal
  # (brain busy, build failed) leaves nothing to mistake for data
  if [ "$DRY_RUN" -eq 0 ] && grep -q '"section": "rows"' "$META" 2>/dev/null; then
    rec section=device temp_end_c="$(temp_c)" thermal_zones_end="$(thermal_zones)" \
      throttled_end="$(throttled)" || true
    rec section=top finished_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)" exit_code:="$rc" || true
    if python3 "$COMPARE" assemble "$META" "$OUT_JSON"; then
      python3 "$COMPARE" --readme "$OUT_JSON" > "$OUT_MD" || true
      log "wrote $OUT_JSON and $OUT_MD"
      if [ "$UPDATE_REGISTRY" -eq 1 ]; then
        python3 "$COMPARE" update-registry "$OUT_JSON" --threads "$(brain_threads)" || true
      fi
    fi
  fi
  rm -rf "$WORK"
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT TERM

check_brain() {
  local rss
  rss=$(llama_server_max_rss_kb)
  if [ "$STOP_BRAIN" -eq 1 ]; then
    if systemctl cat "$BRAIN_UNIT" >/dev/null 2>&1 && systemctl is-active -q "$BRAIN_UNIT"; then
      sudo -n true 2>/dev/null || { echo "--stop-brain needs passwordless sudo (sudo -n) to stop/start $BRAIN_UNIT" >&2; exit 2; }
      log "stopping $BRAIN_UNIT for the run (restarted on exit)"
      sudo -n systemctl stop "$BRAIN_UNIT"
      BRAIN_STOPPED=1
      rec section=brain stop_requested:=true stopped:=true unit="$BRAIN_UNIT"
      sleep 3
      rss=$(llama_server_max_rss_kb)
    else
      rec section=brain stop_requested:=true stopped:=false unit="$BRAIN_UNIT" \
        note="unit not installed or not active"
    fi
  else
    rec section=brain stop_requested:=false stopped:=false unit="$BRAIN_UNIT"
  fi
  if [ -n "$rss" ] && [ "$rss" -gt "$BRAIN_RSS_LIMIT_KB" ]; then
    cat >&2 <<EOF
Refusing to run: a llama-server is using $((rss / 1024)) MB of RAM.
That is the car's brain (or another model server). It competes for the same
CPU cores and memory as the benchmark, so every number would be wrong, and a
large model loaded next to it could push the box into OOM while driving.
Re-run with --stop-brain to stop $BRAIN_UNIT for the run (it is restarted and
health-checked afterwards), or stop the other llama-server yourself.
EOF
    exit 3
  fi
  rec section=brain llama_server_rss_kb_at_start:="${rss:-0}"
}

# ------------------------------------------------------------------ models
shopt -s nullglob nocaseglob
find_model() {   # find_model "glob;glob" -> first matching file path
  local globs=$1 d g f pats
  # split without pathname expansion: with nullglob a bare `for g in $globs`
  # globs each pattern against the CWD first, and a wildcard-only spec vanishes
  IFS=';' read -r -a pats <<< "$globs"
  for g in "${pats[@]}"; do
    for d in "${MODEL_DIRS[@]}"; do
      for f in "$d"/$g; do
        case "$(basename "$f")" in mmproj*|*mmproj*|*.part) continue ;; esac
        [ -f "$f" ] || continue
        [ "$(file_bytes "$f")" -ge "$MIN_MODEL_BYTES" ] || continue
        printf '%s' "$f"; return 0
      done
    done
  done
  return 1
}

file_bytes() {
  # split GGUFs (-00001-of-0000N): count every part
  local f=$1
  case "$f" in
    *-00001-of-*.gguf) stat -c %s "${f%-00001-of-*}"-0000*-of-*.gguf | awk '{ s += $1 } END { print s }' ;;
    *) stat -c %s "$f" ;;
  esac
}

fits() {   # fits BYTES -> 0 when size + headroom <= MemAvailable
  local bytes=$1 avail
  avail=$(( $(meminfo_kb MemAvailable) * 1024 ))
  MEM_AVAIL=$avail
  awk -v b="$bytes" -v a="$avail" -v h="$HEADROOM_GB" 'BEGIN { exit !(b + h * 1e9 <= a) }'
}

drop_cache() {   # drop_cache FILE -> sets CACHE_STATE
  if sudo -n true 2>/dev/null && sync && sudo -n sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null; then
    CACHE_STATE="cold (drop_caches)"
  else
    # no root: ask the kernel to evict just this file; best effort only
    python3 - "$@" <<'PY' 2>/dev/null || true
import os, sys
for p in sys.argv[1:]:
    fd = os.open(p, os.O_RDONLY)
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)
PY
    CACHE_STATE="warm cache possible (no sudo -n; fadvise DONTNEED only)"
  fi
}

cold_load() {   # cold_load BINDIR MODEL -> sets LOAD_S (or empty) and CACHE_STATE
  local bin=$1 model=$2 t0 t1
  LOAD_S=""
  CACHE_STATE="not measured"
  if [ ! -x "$bin/llama-simple" ]; then
    CACHE_STATE="not measured (no llama-simple in this build; pin LLAMA_CPP_REF)"
    return 0
  fi
  drop_cache "$model"
  t0=$(date +%s.%N)
  if timeout 1800 "$bin/llama-simple" -m "$model" -n 1 "Hello" >"$WORK/load.log" 2>&1; then
    t1=$(date +%s.%N)
    LOAD_S=$(awk -v a="$t0" -v b="$t1" 'BEGIN { printf "%.2f", b - a }')
  else
    CACHE_STATE="load run failed: $(tail -1 "$WORK/load.log" | tr -d '"' | cut -c1-120)"
  fi
}

thread_list() {
  if [ "$NPROC" -eq "$BASE_THREADS" ]; then printf '%s' "$BASE_THREADS"
  else printf '%s,%s' "$BASE_THREADS" "$NPROC"; fi
}

# run_bench RUNTIME BINDIR MODEL THREADS ACCEL(true|false) BACKEND NGL -- meta...
run_bench() {
  local runtime=$1 bin=$2 model=$3 threads=$4 accel=$5 backend=$6 ngl=$7; shift 8
  local out="$WORK/bench-$PASS_MODE-$runtime-$backend-$(basename "$model").json"
  log "$runtime $backend: llama-bench $(basename "$model") -t $threads -ngl $ngl"
  "$bin/llama-bench" -m "$model" -p "$N_PROMPT" -n "$N_GEN" -r "$REPS" \
    -t "$threads" -ngl "$ngl" -o json > "$out" 2> "$out.err" || true
  rec section=rows "$@" runtime="$runtime" accel:="$accel" backend="$backend" \
    mode="$PASS_MODE" nproc:="$NPROC" temp_after_c="$(temp_c)" --bench "$out"
}

# NPU hook. Qualcomm's Hexagon NPU needs the QNN / QAIRT SDK, which this
# script deliberately does not install. To add an NPU row, point NPU_RUNNER at
# an executable that takes a GGUF (or converted model) path, benchmarks
# prompt-512 / gen-128 and prints a llama-bench style JSON array on stdout:
# [{"n_prompt":512,"n_gen":0,"avg_ts":..,"stddev_ts":..,"n_threads":<nproc>},
#  {"n_prompt":0,"n_gen":128,...}]. Rows land in the ACCELERATED section as
# backend NPU. TODO: a real runner once a llama.cpp Hexagon backend or a
# QNN path is chosen.
npu_hook() {   # npu_hook ID LABEL MODEL SIZE
  [ -n "${NPU_RUNNER:-}" ] || return 0
  local out="$WORK/npu-$1.json"
  "$NPU_RUNNER" "$3" > "$out" 2> "$out.err" || true
  rec section=rows id="$1" label="$2" file="$(basename "$3")" size_bytes:="$4" \
    runtime="${NPU_RUNTIME:-npu-runner}" accel:=true backend=NPU n_gpu_layers=npu \
    mode="$PASS_MODE" nproc:="$NPROC" --bench "$out"
}

setup_accel() {   # sets ACCEL_DIR / ACCEL_BACKEND, or empties ACCEL on failure
  [ -n "$ACCEL" ] || return 0
  local ref=${LLAMA_CPP_REF:-$DEFAULT_LLAMA_CPP_REF}
  if [ -n "$LLAMA_BIN" ]; then
    ref=$(git -C "$BIN" rev-parse HEAD 2>/dev/null || echo "$ref")
  fi
  ACCEL_DIR="$BENCH_HOME/llama.cpp-$(ref_dir_name "$ref")-$ACCEL"
  case "$ACCEL" in
    vulkan) ACCEL_BACKEND=Vulkan; local extra="-DGGML_VULKAN=ON" ;;
    opencl) ACCEL_BACKEND=OpenCL; local extra="-DGGML_OPENCL=ON -DGGML_OPENCL_USE_ADRENO_KERNELS=ON" ;;
  esac
  if CMAKE_EXTRA="$extra" build_llama "llama.cpp-$ACCEL" "$LLAMA_REPO" "$ref" "$ACCEL_DIR" llama-bench; then
    rec section=runtime name="llama.cpp-$ACCEL" ref="$ref" \
      commit="$(git -C "$ACCEL_DIR" rev-parse HEAD 2>/dev/null || echo unknown)" \
      dir="$ACCEL_DIR" cmake_flags="$(backends_of "$ACCEL_DIR")" status=built
  else
    rec section=runtime name="llama.cpp-$ACCEL" ref="$ref" status=build-failed \
      hint="vulkan needs libvulkan-dev + glslc; opencl needs ocl-icd-opencl-dev + opencl-headers and a working Adreno ICD"
    log "ACCELERATED $ACCEL build failed; continuing with CPU only"
    ACCEL=""
  fi
}

setup_ik() {
  [ "$WITH_IK" -eq 1 ] || return 0
  local ref=${IK_LLAMA_REF:-main}
  IK_DIR="$BENCH_HOME/ik_llama.cpp-$(ref_dir_name "$ref")"
  if CMAKE_EXTRA="" build_llama ik_llama.cpp "$IK_REPO" "$ref" "$IK_DIR" llama-bench; then
    rec section=runtime name=ik_llama.cpp ref="$ref" \
      commit="$(git -C "$IK_DIR" rev-parse HEAD 2>/dev/null || echo unknown)" \
      dir="$IK_DIR" cmake_flags="$(backends_of "$IK_DIR")"
  else
    rec section=runtime name=ik_llama.cpp ref="$ref" status=build-failed
    WITH_IK=0
  fi
}

# ------------------------------------------------------------------ download
DOWNLOAD_FAILED=""   # "id=reason" lines (no associative arrays: bash 3 safe)
dl_failed() { printf '%s\n' "$DOWNLOAD_FAILED" | sed -n "s/^$1=//p" | head -1; }
download_missing() {
  [ "$DOWNLOAD" -eq 1 ] || return 0
  local spec id label globs repo file note dest
  dest=${MODEL_DIRS[0]}
  mkdir -p "$dest"
  for spec in "${MODEL_SPECS[@]}"; do
    IFS='|' read -r id label globs repo file note <<< "$spec"
    selected "$id" || continue
    find_model "$globs" >/dev/null && continue
    if [ -z "$repo" ] || [ -z "$file" ]; then
      log "$id: MISSING and no download source configured in MODEL_SPECS"
      DOWNLOAD_FAILED+="$id=no source configured"$'\n'
      continue
    fi
    log "$id: downloading $repo/$file to $dest (resumes a .part)"
    # .part + mv: a killed download can never leave a truncated model that
    # the glob would then trust (same rule as bench.sh)
    if curl -L --fail -C - -o "$dest/$file.part" "https://huggingface.co/$repo/resolve/main/$file"; then
      mv "$dest/$file.part" "$dest/$file"
      rec section=downloads "$id"="$repo/$file"
    else
      log "$id: download failed (the .part is kept for a resume)"
      DOWNLOAD_FAILED+="$id=curl failed for $repo/$file"$'\n'
    fi
  done
}

# ------------------------------------------------------------------ quality
brain_threads() {   # the thread count the car's brain runs with
  local t=""
  [ -r "$BRAIN_ENV" ] && t=$(sed -n 's/^BRAIN_THREADS=//p' "$BRAIN_ENV" | tr -d '"' | tail -1)
  case "$t" in ''|*[!0-9]*) t=$BASE_THREADS ;; esac
  printf '%s' "$t"
}

# sha256 of a model file, cached in the results dir keyed by name + size +
# mtime: a 15 GB file is hashed once, not on every run.
model_sha256() {
  python3 - "$1" "$OUT_DIR/.model-sha256.cache" <<'SHA'
import hashlib, json, os, sys
path, cache_path = sys.argv[1], sys.argv[2]
st = os.stat(path)
key = f"{os.path.basename(path)}|{st.st_size}|{int(st.st_mtime)}"
try:
    with open(cache_path) as f:
        cache = json.load(f)
except (OSError, ValueError):
    cache = {}
if key not in cache:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    cache[key] = h.hexdigest()
    tmp = cache_path + ".part"
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=1, sort_keys=True)
    os.replace(tmp, cache_path)
print(cache[key])
SHA
}

QUALITY_PID=""
stop_quality_server() {
  [ -n "$QUALITY_PID" ] || return 0
  kill "$QUALITY_PID" 2>/dev/null || true
  wait "$QUALITY_PID" 2>/dev/null || true
  QUALITY_PID=""
}

free_port() {
  python3 -c '
import socket, sys
avoid = int(sys.argv[1])
while True:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    if port != avoid:
        print(port)
        break
' "$QUALITY_PORT_AVOID"
}

# quality_check ID LABEL MODEL: start a llama-server on a free localhost
# port (never the brain's 8081), run bench/quality.py against it, record.
quality_check() {
  local id=$1 label=$2 model=$3 port i up=0 out="$WORK/quality-$1.json"
  if [ ! -x "$BIN/llama-server" ]; then
    rec section=quality id="$id" label="$label" error="no llama-server in $BIN"
    return 0
  fi
  port=$(free_port)
  log "$id: quality checks via llama-server on 127.0.0.1:$port"
  # the brain unit's own flags, so the model answers the way it would in
  # the car; a build without --reasoning gets a plain retry
  local flags=(-m "$model" -t "$(brain_threads)" -c 4096 --host 127.0.0.1 --port "$port")
  "$BIN/llama-server" "${flags[@]}" --reasoning off --reasoning-budget 0 \
    > "$WORK/qserver-$id.log" 2>&1 &
  QUALITY_PID=$!
  sleep 2
  if ! kill -0 "$QUALITY_PID" 2>/dev/null; then
    "$BIN/llama-server" "${flags[@]}" > "$WORK/qserver-$id.log" 2>&1 &
    QUALITY_PID=$!
  fi
  for i in $(seq 1 180); do   # big models off microSD: up to 15 min
    if curl -fsS -m 3 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then up=1; break; fi
    kill -0 "$QUALITY_PID" 2>/dev/null || break
    sleep 5
  done
  if [ "$up" -eq 1 ]; then
    python3 "$HERE/quality.py" --url "http://127.0.0.1:$port" --out "$out" \
      --model-file "$model" --model-sha256 "$(model_sha256 "$model")" \
      --server-commit "$(git -C "$BIN" rev-parse HEAD 2>/dev/null || echo unknown)" || true
    rec section=quality id="$id" label="$label" file="$(basename "$model")" port:="$port" \
      result:=@"$out"
  else
    rec section=quality id="$id" label="$label" \
      error="llama-server did not become healthy: $(tail -1 "$WORK/qserver-$id.log" | tr -d '"' | cut -c1-160)"
  fi
  stop_quality_server
}

# ------------------------------------------------------------------ main
selected() {   # selected ID -> 0 when --only is empty or lists ID
  [ -z "$ONLY" ] && return 0
  case ",$ONLY," in *",$1,"*) return 0 ;; esac
  return 1
}

FOUND_DIR=""
for d in "${MODEL_DIRS[@]}"; do [ -d "$d" ] && { FOUND_DIR=$d; break; }; done

rec section=top started_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  script_commit="$(git -C "$HERE" rev-parse --short HEAD 2>/dev/null || cat "$HERE/../BENCH_COMMIT" 2>/dev/null || echo unknown)" \
  model_dirs:="$(printf '%s\n' "${MODEL_DIRS[@]}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().splitlines()))')"
rec section=settings n_prompt:="$N_PROMPT" n_gen:="$N_GEN" repetitions:="$REPS" \
  threads:="[$(thread_list)]" headroom_gb:="$HEADROOM_GB" only="$ONLY" accel="${ACCEL:-none}"
record_identity

if [ "$DRY_RUN" -eq 1 ]; then
  python3 "$COMPARE" assemble "$META" "$WORK/identity.json"
  cat "$WORK/identity.json"
  for spec in "${MODEL_SPECS[@]}"; do
    IFS='|' read -r id label globs _repo _file _note <<< "$spec"
    selected "$id" || continue
    if f=$(find_model "$globs"); then
      b=$(file_bytes "$f")
      if fits "$b"; then st=RUN; else st=SKIPPED-NOFIT; fi
      printf '%-12s %-14s %6.1f GB  %s\n' "$id" "$st" "$(awk -v b="$b" 'BEGIN { print b / 1e9 }')" "$f"
    else
      printf '%-12s %-14s\n' "$id" MISSING
    fi
  done
  exit 0
fi

download_missing
check_brain
setup_runtime
setup_ik
setup_accel
THREADS=$(thread_list)
rec section=services modes="${MODES[*]}" brain_threads:="$(brain_threads)"

QUALITY_MODELS=()
LAST_MODE=${MODES[${#MODES[@]}-1]}
for PASS_MODE in "${MODES[@]}"; do
  [ "$PASS_MODE" = clean ] && stop_clean_units
  check_busy_others
  log "pass: $PASS_MODE"
  for spec in "${MODEL_SPECS[@]}"; do
    IFS='|' read -r id label globs _repo _file spec_note <<< "$spec"
    selected "$id" || continue
    if ! f=$(find_model "$globs"); then
      # MISSING does not depend on the pass: record it once, without a mode
      if [ "$PASS_MODE" = "${MODES[0]}" ]; then
        why=$(dl_failed "$id")
        log "$id: MISSING (no file matching $globs in ${MODEL_DIRS[*]})"
        rec section=rows id="$id" label="$label" status=MISSING runtime=llama.cpp \
          accel:=false thread_modes:='[]' spec_note="$spec_note" \
          note="no file matching $globs${why:+; download failed: $why}"
      fi
      continue
    fi
    bytes=$(file_bytes "$f")
    if ! fits "$bytes"; then
      log "$id: SKIPPED-NOFIT in $PASS_MODE pass ($bytes B + ${HEADROOM_GB} GB > MemAvailable $MEM_AVAIL B)"
      rec section=rows id="$id" label="$label" file="$(basename "$f")" size_bytes:="$bytes" \
        spec_note="$spec_note" status=SKIPPED-NOFIT runtime=llama.cpp accel:=false thread_modes:='[]' \
        mode="$PASS_MODE" mem_available_bytes:="$MEM_AVAIL" note="needs size + ${HEADROOM_GB} GB"
      continue
    fi
    t_before=$(temp_c)
    cold_load "$BIN" "$f"
    common=(id="$id" label="$label" file="$(basename "$f")" size_bytes:="$bytes" spec_note="$spec_note"
            temp_before_c="$t_before" mem_available_bytes:="$MEM_AVAIL")
    load=(load_cache="$CACHE_STATE")
    [ -n "$LOAD_S" ] && load+=(load_s:="$LOAD_S")
    run_bench llama.cpp "$BIN" "$f" "$THREADS" false CPU 0 -- "${common[@]}" "${load[@]}"
    if [ "$WITH_IK" -eq 1 ]; then
      run_bench ik_llama.cpp "$IK_DIR/build/bin" "$f" "$THREADS" false CPU 0 -- "${common[@]}"
    fi
    if [ -n "$ACCEL" ]; then
      run_bench llama.cpp "$ACCEL_DIR/build/bin" "$f" "$NPROC" true "$ACCEL_BACKEND" 99 -- "${common[@]}"
    fi
    npu_hook "$id" "$label" "$f" "$bytes"
    if [ "$PASS_MODE" = "$LAST_MODE" ] && [ "$QUALITY" -eq 1 ]; then
      QUALITY_MODELS+=("$id|$label|$f")
    fi
  done
done

# Answer quality is a property of the model + prompt, not of the pass:
# checked once per model, after the speed runs.
for q in ${QUALITY_MODELS[@]+"${QUALITY_MODELS[@]}"}; do
  IFS='|' read -r id label f <<< "$q"
  quality_check "$id" "$label" "$f"
done

log "done; compare boards with: python3 $COMPARE $OUT_DIR/<board-a>.json $OUT_DIR/<board-b>.json"
