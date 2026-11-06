#!/usr/bin/env bash
# Generate a self-signed TLS certificate for the DPS/IoT Hub emulator.
#
# The ventilator firmware does NOT validate the server certificate (no CA
# check, no host name check), so a self-signed cert is sufficient. Its only
# job is to let the TLS handshake complete. A long validity (10 years) avoids
# silent breakage later; nothing relies on it for trust.
#
# Usage: EMU_HUB_HOST=vent.example.com ./emulator/gen-cert.sh [OUT_DIR]
#        OUT_DIR defaults to ./data (the directory docker-compose mounts).
set -euo pipefail

: "${EMU_HUB_HOST:?set EMU_HUB_HOST to the host name the device will connect to}"
OUT_DIR="${1:-./data}"
mkdir -p "$OUT_DIR"

if [[ -e "$OUT_DIR/cert.pem" || -e "$OUT_DIR/key.pem" ]]; then
  echo "cert.pem/key.pem already exist in $OUT_DIR; remove them first to regenerate." >&2
  exit 1
fi

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$OUT_DIR/key.pem" -out "$OUT_DIR/cert.pem" \
  -days 3650 \
  -subj "/CN=${EMU_HUB_HOST}" \
  -addext "subjectAltName=DNS:${EMU_HUB_HOST}"

chmod 600 "$OUT_DIR/key.pem"
echo "wrote $OUT_DIR/cert.pem and $OUT_DIR/key.pem (CN=${EMU_HUB_HOST}, 10 years)"
