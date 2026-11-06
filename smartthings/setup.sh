#!/usr/bin/env bash
# Create the SmartThings virtual device for the ventilator.
#
#   1. device-config create          -> presentation id (vid) + manufacturerName (mnmn)
#   2. device profile create with metadata.vid/mnmn baked in
#      (a NEW profile every run: updating an existing profile's vid is not
#       picked up by new devices - the old presentation stays cached)
#   3. virtualdevices:create in the given location/room
#
# Initial values are seeded by the bridge on its first telemetry push.
#
# Usage: ./smartthings/setup.sh --location <locationId> [--room <roomId>] [--name "환기"]
# Requires: smartthings CLI (logged in), python3.
# Find ids with: smartthings locations / smartthings locations:rooms <locationId>
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LOCATION="" ROOM="" NAME="환기"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --location) LOCATION="$2"; shift 2 ;;
    --room)     ROOM="$2"; shift 2 ;;
    --name)     NAME="$2"; shift 2 ;;
    -h|--help)  sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$LOCATION" ]] || { echo "--location is required" >&2; exit 2; }
command -v smartthings >/dev/null || { echo "smartthings CLI not found" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "[1/3] creating device config"
smartthings presentation:device-config:create -j -i "$HERE/device-config.json" -o "$WORK/config_out.json"
read -r VID MNMN < <(python3 - "$WORK/config_out.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(d["presentationId"], d.get("manufacturerName", "SmartThingsCommunity"))
PY
)
echo "      vid=$VID mnmn=$MNMN"

echo "[2/3] creating device profile with that vid"
python3 - "$HERE/profile.json" "$WORK/profile.json" "$VID" "$MNMN" <<'PY'
import json, sys, time
src, dst, vid, mnmn = sys.argv[1:5]
p = json.load(open(src))
p["name"] = "%s-%d" % (p["name"], int(time.time()))  # profile names must be unique
p["metadata"] = {"vid": vid, "mnmn": mnmn}
json.dump(p, open(dst, "w"), ensure_ascii=False)
PY
smartthings deviceprofiles:create -j -i "$WORK/profile.json" -o "$WORK/profile_out.json"
PROFILE_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$WORK/profile_out.json")"
echo "      profile=$PROFILE_ID"

echo "[3/3] creating virtual device"
python3 - "$WORK/device.json" "$NAME" "$LOCATION" "$ROOM" "$PROFILE_ID" <<'PY'
import json, sys
dst, name, loc, room, prof = sys.argv[1:6]
d = {"name": name, "owner": {"ownerType": "LOCATION", "ownerId": loc}, "deviceProfileId": prof}
if room:
    d["roomId"] = room
json.dump(d, open(dst, "w"), ensure_ascii=False)
PY
smartthings virtualdevices:create -j -i "$WORK/device.json" -o "$WORK/device_out.json"
DEVICE_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["deviceId"])' "$WORK/device_out.json")"

cat <<EOF

Created virtual device: $DEVICE_ID
Put it in .env as:  ST_DEVICE_ID=$DEVICE_ID
Note: SmartThings fills every attribute with a random value at creation; one such
entry stays in the device history. The bridge overwrites them on its first push.
EOF
