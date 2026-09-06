#!/usr/bin/env bash
# install.sh — put the three model checkers on this machine, idempotently.
#
# Everything lands under $LOOPKIT_TOOLS_DIR (default ~/.loopkit/tools), which
# is also where driver.mjs looks by default. Override with
# LOOPKIT_TOOLS_DIR=/somewhere/else (or TOOLS_DIR, the older name). The FizzBee
# tarball alone unpacks to ~67 MB, so point it at a disk with room.
#
# Every download pins an exact release tag and refuses to leave https:
# `--proto '=https' --proto-redir '=https'` means a redirect to http fails
# rather than silently downgrading. There is still no checksum pinning — the
# tags are the only integrity claim, which is worth knowing before running
# this on anything that matters.
#
# Verified end to end on darwin/arm64. The Linux asset names are selected
# below but have NOT been exercised; they are ~300 MB where the macOS builds
# are ~33 MB.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="${LOOPKIT_TOOLS_DIR:-${TOOLS_DIR:-$HOME/.loopkit/tools}}"
FIZZ_VER="v0.5.3"
TLA_VER="v1.7.4"
QUINT_VER="0.32.0"
CURL="curl -fsSL --proto =https --proto-redir =https"

# FizzBee ships one asset per platform and the names are not uniform.
# driver.mjs mirrors this table in fizzAsset().
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)        FIZZ_ASSET="fizzbee-${FIZZ_VER}-macos_arm" ;;
  Darwin-x86_64)       FIZZ_ASSET="fizzbee-${FIZZ_VER}-macos_x86" ;;
  Linux-aarch64|Linux-arm64) FIZZ_ASSET="fizzbee-${FIZZ_VER}-linux_arm" ;;
  Linux-x86_64)        FIZZ_ASSET="fizzbee-${FIZZ_VER}-linux_x86" ;;
  *)
    echo "No FizzBee release asset for $(uname -s)-$(uname -m)." >&2
    echo "Build from source at tag ${FIZZ_VER}, then set FIZZ_BIN." >&2
    exit 2
    ;;
esac

for tool in node npm java curl tar; do
  command -v "$tool" >/dev/null 2>&1 || { echo "missing: $tool (needed by install.sh)" >&2; exit 2; }
done

mkdir -p "$TOOLS_DIR/fizzbee" "$TOOLS_DIR/tlaplus" "$TOOLS_DIR/quint"

# --- FizzBee (Go binary + a bazel-packed Python parser) ---------------------
if [ ! -x "$TOOLS_DIR/fizzbee/$FIZZ_ASSET/fizz" ]; then
  echo "==> fizzbee $FIZZ_VER ($FIZZ_ASSET)"
  $CURL -o "$TOOLS_DIR/fizzbee/$FIZZ_ASSET.tar.gz" \
    "https://github.com/fizzbee-io/fizzbee/releases/download/$FIZZ_VER/$FIZZ_ASSET.tar.gz"
  tar xzf "$TOOLS_DIR/fizzbee/$FIZZ_ASSET.tar.gz" -C "$TOOLS_DIR/fizzbee"
else
  echo "==> fizzbee already at $TOOLS_DIR/fizzbee/$FIZZ_ASSET"
fi

# --- TLA+ (TLC lives in one jar; needs a JVM, 17+ is fine) ------------------
if [ ! -f "$TOOLS_DIR/tlaplus/tla2tools.jar" ]; then
  echo "==> tla2tools $TLA_VER"
  $CURL -o "$TOOLS_DIR/tlaplus/tla2tools.jar" \
    "https://github.com/tlaplus/tlaplus/releases/download/$TLA_VER/tla2tools.jar"
else
  echo "==> tla2tools already at $TOOLS_DIR/tlaplus/tla2tools.jar"
fi

# --- Quint (npm; pulls its Rust evaluator into ~/.quint on first run) -------
if [ ! -x "$TOOLS_DIR/quint/node_modules/.bin/quint" ]; then
  echo "==> quint $QUINT_VER"
  npm install --prefix "$TOOLS_DIR/quint" "@informalsystems/quint@$QUINT_VER"
else
  echo "==> quint already at $TOOLS_DIR/quint/node_modules/.bin/quint"
fi

echo
echo "Installed under $TOOLS_DIR. Verify — doctor probes each engine's OUTPUT, not just its path:"
echo "  LOOPKIT_TOOLS_DIR=\"$TOOLS_DIR\" node \"$HERE/driver.mjs\" doctor"
