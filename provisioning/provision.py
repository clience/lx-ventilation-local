#!/usr/bin/env python3
"""Provision the ventilator's Wi-Fi and DPS endpoint over its setup AP.

1. Put the ventilator in pairing mode; it opens an AP named ``lxhsv_xxxxxx``.
2. Join that AP from this computer (see README for the AP password rule).
3. Run this script. It talks to the device at 192.168.1.2:8080 (defaults).

Wire format (both directions):
    b"LGHAUSYS" | uint32 big-endian JSON length | JSON (ASCII)
Sequence:
    LG100  {"type":"REQ","code":"LG100"}                       -> device info
    LG200  {"type":"REQ","code":"LG200","deviceId":[id],"apSsid","apPassword",
            "dpsKey","dpsEndpoint","idScope"}                    -> stores settings
    LG300  {"type":"REQ","code":"LG300"}                       -> applies, leaves AP mode
Each reply carries "result": "200" on success.

Example:
    WIFI_PASSWORD='...' python3 provision.py --ssid MyHomeWiFi \
        --dps-endpoint vent.example.com --device-id my-vent-1 --dry-run
"""
import argparse
import base64
import json
import os
import secrets
import socket
import struct
import sys

MAGIC = b"LGHAUSYS"
SECRET_FIELDS = ("apPassword", "dpsKey")


def encode_frame(msg):
    raw = json.dumps(msg, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return MAGIC + struct.pack(">I", len(raw)) + raw


def decode_frame(buf):
    if len(buf) < 12:
        raise ValueError("short frame (%d bytes)" % len(buf))
    if buf[:8] != MAGIC:
        raise ValueError("bad magic %r" % buf[:8])
    (n,) = struct.unpack(">I", buf[8:12])
    return json.loads(buf[12:12 + n].decode("utf-8", "replace"))


def masked(msg):
    out = dict(msg)
    for k in SECRET_FIELDS:
        if out.get(k):
            out[k] = "*" * 8
    return out


def build_messages(a):
    return [
        {"type": "REQ", "code": "LG100"},
        {"type": "REQ", "code": "LG200", "deviceId": [a.device_id], "apSsid": a.ssid,
         "apPassword": a.password, "dpsKey": a.dps_key, "dpsEndpoint": a.dps_endpoint,
         "idScope": a.id_scope},
        {"type": "REQ", "code": "LG300"},
    ]


def recv_frame(sock):
    buf = b""
    while len(buf) < 12:
        c = sock.recv(4096)
        if not c:
            break
        buf += c
    if len(buf) >= 12:
        (n,) = struct.unpack(">I", buf[8:12])
        while len(buf) < 12 + n:
            c = sock.recv(4096)
            if not c:
                break
            buf += c
    return decode_frame(buf)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    env = os.environ.get
    p.add_argument("--host", default=env("DEVICE_AP_HOST", "192.168.1.2"), help="device IP on its setup AP")
    p.add_argument("--port", type=int, default=int(env("DEVICE_AP_PORT", "8080")))
    p.add_argument("--ssid", default=env("WIFI_SSID"), help="home Wi-Fi SSID (2.4 GHz)")
    p.add_argument("--password", default=env("WIFI_PASSWORD"),
                   help="home Wi-Fi password (prefer the WIFI_PASSWORD env var)")
    p.add_argument("--dps-endpoint", default=env("DPS_ENDPOINT"),
                   help="host name of your emulator (same as EMU_HUB_HOST)")
    p.add_argument("--id-scope", default=env("DPS_ID_SCOPE", "0ne00000000"),
                   help="DPS ID scope; the emulator ignores it (default: 0ne00000000)")
    p.add_argument("--device-id", default=env("DEVICE_ID", "lx-vent-1"),
                   help="registration/device id the device will use (default: lx-vent-1)")
    p.add_argument("--dps-key", default=env("DPS_KEY"),
                   help="DPS symmetric key; the emulator does not verify it (default: random)")
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--dry-run", action="store_true", help="print the frames, send nothing")
    a = p.parse_args(argv)
    missing = [n for n in ("ssid", "password", "dps_endpoint") if not getattr(a, n)]
    if missing:
        p.error("missing: " + ", ".join("--" + m.replace("_", "-") for m in missing))
    if not a.dps_key:
        a.dps_key = base64.b64encode(secrets.token_bytes(32)).decode()
    for field in ("ssid", "password", "dps_endpoint", "device_id"):
        try:
            getattr(a, field).encode("ascii")
        except UnicodeEncodeError:
            p.error("--%s must be ASCII (the device's framing is byte-per-char)" % field.replace("_", "-"))
    return a


def main(argv=None):
    a = parse_args(argv)
    msgs = build_messages(a)
    if a.dry_run:
        for m in msgs:
            frame = encode_frame(m)
            print("%s  (%d bytes) %s" % (m["code"], len(frame), json.dumps(masked(m), ensure_ascii=False)))
        return 0

    print("connecting to %s:%d ..." % (a.host, a.port))
    with socket.create_connection((a.host, a.port), timeout=a.timeout) as s:
        s.settimeout(a.timeout)
        for m in msgs:
            print("-> %s %s" % (m["code"], json.dumps(masked(m), ensure_ascii=False)))
            s.sendall(encode_frame(m))
            try:
                resp = recv_frame(s)
            except (socket.timeout, ValueError) as e:
                if m["code"] == "LG300":
                    # the device may drop the AP right after applying; treat as done
                    print("<- (no reply to LG300: %s) - device is probably rebooting onto Wi-Fi" % e)
                    break
                print("<- error: %s" % e, file=sys.stderr)
                return 1
            print("<- %s" % json.dumps(masked(resp), ensure_ascii=False))
            if str(resp.get("result")) != "200":
                print("device rejected %s" % m["code"], file=sys.stderr)
                return 1
    print("done. The device should now join Wi-Fi and connect to %s:8883." % a.dps_endpoint)
    return 0


if __name__ == "__main__":
    sys.exit(main())
