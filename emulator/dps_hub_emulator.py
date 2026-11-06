#!/usr/bin/env python3
"""Minimal self-hosted Azure DPS + IoT Hub MQTT emulator for the LX Z:IN ventilator.

The ventilator does not validate the server's TLS certificate, so a self-signed
certificate is enough. One TCP port (default 8883) serves both roles:

  1. DPS:  device CONNECTs with a username containing ``/registrations/``,
           publishes ``$dps/registrations/PUT/iotdps-register/?$rid=N`` and we
           answer ``status=assigned`` with ``assignedHub = EMU_HUB_HOST``.
  2. HUB:  device reconnects as ``<EMU_HUB_HOST>/<deviceId>/...``, subscribes to
           twin/method topics, GETs its twin and starts publishing telemetry to
           ``devices/<id>/messages/events/``.

Every PUBLISH received in the HUB role is appended to ``$DATA_DIR/raw.log`` as
``HH:MM:SS <topic> | <payload>``. To control the device, write
``{"desired": {...}}`` to ``$DATA_DIR/cmd.json``; it is delivered as a desired
twin PATCH on the live connection (checked every 0.5 s) and the file is removed.

Environment:
  EMU_HUB_HOST  (required) host name the device must reach for IoT Hub;
                normally the same name you give as dpsEndpoint.
  DATA_DIR      directory for raw.log / telemetry.log / cmd.json (default ./data)
  EMU_CERT      TLS certificate (default $DATA_DIR/cert.pem)
  EMU_KEY       TLS private key (default $DATA_DIR/key.pem)
  EMU_PORT      listen port (default 8883)
  RAW_MAX_BYTES rotate raw.log/telemetry.log to *.1 above this size (default 5 MiB)

Standard library only. This is a protocol shim, not a general MQTT broker:
QoS 0 delivery, one client per connection, no retained messages.
"""
import datetime
import json
import os
import re
import socket
import ssl
import struct
import sys
import threading
import time

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.getcwd(), "data"))
CERT = os.environ.get("EMU_CERT", os.path.join(DATA_DIR, "cert.pem"))
KEY = os.environ.get("EMU_KEY", os.path.join(DATA_DIR, "key.pem"))
PORT = int(os.environ.get("EMU_PORT", "8883"))
LISTEN = os.environ.get("EMU_LISTEN", "0.0.0.0")
HUB_HOST = os.environ.get("EMU_HUB_HOST", "")
RAW_MAX_BYTES = int(os.environ.get("RAW_MAX_BYTES", str(5 * 1024 * 1024)))

CMD = os.path.join(DATA_DIR, "cmd.json")
RAW = os.path.join(DATA_DIR, "raw.log")
TELE = os.path.join(DATA_DIR, "telemetry.log")

POLL = 0.5           # how often an idle HUB connection checks cmd.json
MIN_IDLE_LIMIT = 600  # never drop an idle-but-alive link sooner than this (s)
DESIRED_TOPIC = "$iothub/twin/PATCH/properties/desired/?$version=2"

_file_lock = threading.Lock()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- MQTT bits
def encode_remaining_length(n):
    out = b""
    while True:
        d = n % 128
        n //= 128
        if n:
            d |= 0x80
        out += bytes([d])
        if not n:
            return out


def mqtt_publish(topic, payload=b""):
    """Build a QoS 0 PUBLISH packet."""
    if isinstance(payload, str):
        payload = payload.encode()
    tb = topic.encode()
    body = struct.pack(">H", len(tb)) + tb + payload
    return bytes([0x30]) + encode_remaining_length(len(body)) + body


def recv_exact(sock, n):
    b = b""
    while len(b) < n:
        c = sock.recv(n - len(b))
        if not c:
            return None
        b += c
    return b


def read_remaining_length(sock):
    mult, val = 1, 0
    while True:
        c = sock.recv(1)
        if not c:
            return None
        val += (c[0] & 0x7F) * mult
        if not c[0] & 0x80:
            return val
        mult *= 128


def parse_publish(body, qos):
    tl = struct.unpack(">H", body[:2])[0]
    topic = body[2:2 + tl].decode("utf-8", "replace")
    rest = body[2 + tl:]
    pid = None
    if qos > 0:
        pid = struct.unpack(">H", rest[:2])[0]
        rest = rest[2:]
    return topic, pid, rest


def connect_keepalive(body):
    """Keepalive seconds from a CONNECT variable header (0 if unparsable)."""
    try:
        pn = struct.unpack(">H", body[:2])[0]
        return struct.unpack(">H", body[2 + pn + 2:2 + pn + 4])[0]
    except Exception:
        return 0


def rid_of(topic):
    m = re.search(r"\$rid=([^&]+)", topic)
    return m.group(1) if m else "1"


# ---------------------------------------------------------------- files
def append_capped(path, line):
    with _file_lock:
        try:
            if os.path.exists(path) and os.path.getsize(path) > RAW_MAX_BYTES:
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            log("write err", path, repr(e)[:80])


def read_cmd(remove):
    """Return the pending desired dict (or None). Optionally consume the file."""
    with _file_lock:
        if not os.path.exists(CMD):
            return None
        try:
            with open(CMD, encoding="utf-8") as f:
                c = json.load(f)
        except Exception as e:
            log("cmd.json unreadable", repr(e)[:80])
            c = None
        if remove:
            try:
                os.remove(CMD)
            except FileNotFoundError:
                pass
    if c is None:
        return None
    return c.get("desired", c)


def push_pending(tls, why):
    des = read_cmd(remove=True)
    if des is None:
        return
    tls.sendall(mqtt_publish(DESIRED_TOPIC, json.dumps(des)))
    log("[HUB] -> pushed desired (%s) %s" % (why, json.dumps(des, ensure_ascii=False)))


# ---------------------------------------------------------------- session
def registration_state(devid, full=True):
    st = {"registrationId": devid, "assignedHub": HUB_HOST, "deviceId": devid, "status": "assigned"}
    if full:
        st.update({
            "substatus": "initialAssignment",
            "lastUpdatedUtc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "etag": "emu",
        })
    return {"operationId": "emu-op-1", "status": "assigned", "registrationState": st}


def handle(conn, addr, ctx):
    peer = "%s:%d" % addr[:2]
    try:
        tls = ctx.wrap_socket(conn, server_side=True)
    except Exception as e:
        log("TLS rejected from", peer, repr(e)[:80])
        conn.close()
        return
    role, devid = "?", "unknown-device"
    try:
        h = recv_exact(tls, 1)
        if not h or (h[0] >> 4) != 1:  # first packet must be CONNECT
            return
        rl = read_remaining_length(tls)
        body = recv_exact(tls, rl) or b""
        txt = body.decode("utf-8", "replace")
        role = "DPS" if "/registrations/" in txt else "HUB"
        m = re.search(r"registrations/([^/&]+)", txt) or re.search(r"%s/([^/?]+)" % re.escape(HUB_HOST), txt)
        if m:
            devid = m.group(1)
        log("[%s] CONNECT from %s devid=%s" % (role, peer, devid))
        tls.sendall(bytes([0x20, 0x02, 0x00, 0x00]))  # CONNACK, accepted

        ka = connect_keepalive(body)
        limit = max(MIN_IDLE_LIMIT, ka * 2 if ka else MIN_IDLE_LIMIT)
        idle = 0.0
        tls.settimeout(POLL)
        log("[%s] keepalive=%ss idle-limit=%ss" % (role, ka, limit))

        while True:
            try:
                h = tls.recv(1)
            except socket.timeout:
                idle += POLL
                if idle >= limit:
                    log("[%s] idle timeout %s" % (role, peer))
                    break
                if role == "HUB":
                    push_pending(tls, "poll")
                continue
            except Exception:
                break
            if not h:
                break
            idle = 0.0
            tls.settimeout(10)
            pt, flags = h[0] >> 4, h[0] & 0x0F
            rl = read_remaining_length(tls)
            if rl is None:
                break
            body = (recv_exact(tls, rl) or b"") if rl else b""
            tls.settimeout(POLL)

            if pt == 8:  # SUBSCRIBE
                pid = struct.unpack(">H", body[:2])[0]
                filters = re.sub(r"[^ -~]", " ", body[2:].decode("utf-8", "replace"))
                log("[%s] SUBSCRIBE %s" % (role, filters[:120]))
                tls.sendall(bytes([0x90, 0x03]) + struct.pack(">H", pid) + bytes([0x00]))
                if role == "HUB" and "desired" in filters:
                    push_pending(tls, "on subscribe")
            elif pt == 12:  # PINGREQ
                tls.sendall(bytes([0xD0, 0x00]))
            elif pt == 3:  # PUBLISH
                qos = (flags >> 1) & 3
                topic, pid, payload = parse_publish(body, qos)
                if qos > 0:
                    tls.sendall(bytes([0x40, 0x02]) + struct.pack(">H", pid))  # PUBACK
                text = payload.decode("utf-8", "replace")
                if role == "HUB":
                    append_capped(RAW, "%s %s | %s\n" % (time.strftime("%H:%M:%S"), topic, text))
                handle_publish(tls, role, devid, topic, text)
                if role == "HUB":
                    push_pending(tls, "after publish")
            elif pt == 14:  # DISCONNECT
                log("[%s] DISCONNECT" % role)
                break
            else:
                log("[%s] pkt type=%d len=%d" % (role, pt, rl))
    except Exception as e:
        log("[%s] err %s %s" % (role, peer, repr(e)[:100]))
    finally:
        try:
            tls.close()
        except Exception:
            pass


def handle_publish(tls, role, devid, topic, text):
    if role == "DPS" and "iotdps-register" in topic:
        tls.sendall(mqtt_publish("$dps/registrations/res/200/?$rid=%s" % rid_of(topic),
                                 json.dumps(registration_state(devid))))
        log("[DPS] -> assigned, assignedHub=%s (device will reconnect as HUB)" % HUB_HOST)
    elif role == "DPS" and "iotdps-get-operationstatus" in topic:
        tls.sendall(mqtt_publish("$dps/registrations/res/200/?$rid=%s" % rid_of(topic),
                                 json.dumps(registration_state(devid, full=False))))
        log("[DPS] -> operation status: assigned")
    elif role == "HUB" and topic.startswith("$iothub/twin/GET"):
        des = read_cmd(remove=False)
        twin = {"desired": {"$version": 1}, "reported": {"$version": 1}}
        if des is not None:
            # embed a pending command so the device applies it straight from the twin
            twin["desired"] = dict(des, **{"$version": 2})
        tls.sendall(mqtt_publish("$iothub/twin/res/200/?$rid=%s" % rid_of(topic), json.dumps(twin)))
        log("[HUB] twin GET -> twin (pending cmd=%s)" % (des is not None))
        push_pending(tls, "on twin GET")
    elif role == "HUB" and "messages/events" in topic:
        log("[HUB] TELEMETRY %s" % text[:400])
        append_capped(TELE, "%s %s\n" % (time.strftime("%H:%M:%S"), text))
    elif role == "HUB" and "twin/reported" in topic:
        log("[HUB] REPORTED %s" % text[:400])
        append_capped(TELE, "%s REPORTED %s\n" % (time.strftime("%H:%M:%S"), text))
    else:
        log("[%s] PUBLISH %s | %s" % (role, topic[:90], text[:120]))


def main():
    if not HUB_HOST:
        sys.exit("EMU_HUB_HOST is required (the host name the device resolves to this server)")
    os.makedirs(DATA_DIR, exist_ok=True)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((LISTEN, PORT))
    s.listen(8)
    log("DPS+Hub emulator on %s:%d assignedHub=%s data=%s" % (LISTEN, PORT, HUB_HOST, DATA_DIR))
    while True:
        c, a = s.accept()
        threading.Thread(target=handle, args=(c, a, ctx), daemon=True).start()


if __name__ == "__main__":
    main()
