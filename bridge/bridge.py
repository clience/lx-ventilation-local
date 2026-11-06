#!/usr/bin/env python3
"""Bridge between the local DPS/IoT Hub emulator and a SmartThings virtual device.

Telemetry  emulator raw.log  -> parse latest values -> SmartThings device events
Commands   SmartThings status (polled) -> user change detected -> cmd.json -> emulator

Environment:
  ST_DEVICE_ID    (required) SmartThings virtual device id
  DATA_DIR        shared directory with the emulator (default ./data)
  ST_TOKEN_FILE   access token file (default $DATA_DIR/st_token.txt)
  ST_REFRESH_FILE refresh token file (default $DATA_DIR/st_refresh.txt)
  ST_CLIENT_ID    OAuth client id the refresh token was issued to (needed to refresh)
  CMD_POLL        seconds between SmartThings status polls (default 1)
  TELE_EVERY      seconds between telemetry pushes (default 10)
  RAW_TAIL_KB     only the last N KiB of raw.log are parsed (default 256)

Device layout (single "main" component, standard capabilities only):
  switch=power, mode=auto/turbo/sleep, fanSpeed=1..3, rapidCooling=lamp,
  dustSensor.fineDustLevel=PM2.5, tvocMeasurement=TVOC index, carbonDioxideMeasurement,
  temperatureMeasurement, relativeHumidityMeasurement,
  filterState=filter life %, ultravioletIndex=heat-exchanger life % (relabelled by device-config)
"""
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.getcwd(), "data"))
RAW = os.path.join(DATA_DIR, "raw.log")
CMD = os.path.join(DATA_DIR, "cmd.json")
TOKF = os.environ.get("ST_TOKEN_FILE", os.path.join(DATA_DIR, "st_token.txt"))
RTF = os.environ.get("ST_REFRESH_FILE", os.path.join(DATA_DIR, "st_refresh.txt"))
CLIENT_ID = os.environ.get("ST_CLIENT_ID", "")
RAW_TAIL_BYTES = int(os.environ.get("RAW_TAIL_KB", "256")) * 1024
API = "https://api.smartthings.com/v1"
TOKEN_URL = "https://api.smartthings.com/oauth/token"

# device mode code <-> SmartThings mode value (device-config relabels these 자동/터보/취침)
MODE_N2K = {"2": "auto", "3": "turbo", "4": "sleep"}
MODE_K2N = {v: k for k, v in MODE_N2K.items()}

_JSON_TAIL = re.compile(r"\| (\{.*\})\s*$")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- SmartThings API
def token():
    with open(TOKF, encoding="utf-8") as f:
        return f.read().strip()


def refresh_token():
    """Exchange the refresh token. SmartThings rotates it: the old one is single-use."""
    if not CLIENT_ID:
        log("ST_CLIENT_ID not set; cannot refresh")
        return False
    try:
        with open(RTF, encoding="utf-8") as f:
            rt = f.read().strip()
    except OSError:
        log("no refresh token file")
        return False
    data = urllib.parse.urlencode({"grant_type": "refresh_token", "client_id": CLIENT_ID,
                                   "refresh_token": rt}).encode()
    req = urllib.request.Request(TOKEN_URL, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=15).read())
        _write_atomic(TOKF, r["access_token"])
        _write_atomic(RTF, r["refresh_token"])
        log("token refreshed (expires_in=%s)" % r.get("expires_in"))
        return True
    except Exception as e:
        log("token refresh FAILED", repr(e)[:120])
        return False


def api(method, path, body=None, _retry=True):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 401 and _retry and refresh_token():
            return api(method, path, body, _retry=False)
        log("API %s %s -> %s %s" % (method, path, e.code, e.read()[:120]))
    except Exception as e:
        log("API err", repr(e)[:100])
    return None


def push_events(dev, evs):
    for i in range(0, len(evs), 8):
        api("POST", "/virtualdevices/%s/events" % dev, {"deviceEvents": evs[i:i + 8]})


# ---------------------------------------------------------------- telemetry
def read_tail_lines(path, max_bytes=None):
    """Last ``max_bytes`` of a file as complete lines (first partial line dropped)."""
    max_bytes = RAW_TAIL_BYTES if max_bytes is None else max_bytes
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - max_bytes)
            f.seek(start)
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]
    return lines


def remaining_pct(usage, life):
    """Remaining life %: floor(100 - usage/life*100), clamped to 0..100."""
    if not life:
        return None
    return max(0, min(100, math.floor(100 - (usage or 0) * 100 / life)))


def parse_lines(lines):
    """Fold raw.log lines (oldest first) into a dict of latest known values."""
    st = {}
    for ln in lines:
        m = _JSON_TAIL.search(ln)
        if not m:
            continue
        try:
            o = json.loads(m.group(1))
        except ValueError:
            continue
        if not isinstance(o, dict):
            continue
        sensor = o.get("sensor") if isinstance(o.get("sensor"), dict) else {}
        ac = sensor.get("airCondition")
        if isinstance(ac, dict):
            for src, dst in (("co2", "co2"), ("pm2_5", "pm2_5"), ("tvocs", "tvocs"),
                             ("temperature", "temp"), ("humidity", "hum")):
                if ac.get(src) is not None:
                    st[dst] = ac[src]
        pw = sensor.get("power")
        if isinstance(pw, dict) and "status" in pw:
            st["power"] = pw["status"]
        # periodic sensorStatus report and command echo ({"operation":"updated","sensor":{...}})
        status = o.get("sensorStatus") if isinstance(o.get("sensorStatus"), dict) else {}
        for ss in (status, sensor):
            fan = ss.get("fan")
            if isinstance(fan, dict) and fan.get("fanSpeed") is not None:
                st["fan"] = fan["fanSpeed"]
            lamp = ss.get("lamp")
            if isinstance(lamp, dict) and "style" in lamp:
                st["lamp"] = "on" if lamp["style"] and lamp["style"] != "none" else "off"
            if "mode" in ss:
                md = ss["mode"]
                md = md.get("currentMode") if isinstance(md, dict) else md
                if str(md) in MODE_N2K:  # 0 (off), 5 (manual), 6 ... keep the last shown mode
                    st["mode"] = MODE_N2K[str(md)]
        fe = sensor.get("filter") if isinstance(sensor.get("filter"), dict) else {}
        he = sensor.get("heatExchanger") if isinstance(sensor.get("heatExchanger"), dict) else {}
        p = remaining_pct(fe.get("filterUsageTime"), fe.get("filterLifeTime"))
        if p is not None:
            st["filterPct"] = p
        p = remaining_pct(fe.get("cleanUsageTime"), fe.get("cleanLifeTime"))
        if p is not None:
            st["cleanPct"] = p
        p = remaining_pct(he.get("usageTime"), he.get("lifeTime"))
        if p is not None:
            st["hexPct"] = p
    return st


def tail_sensor_state(path=None):
    return parse_lines(read_tail_lines(path or RAW))


def to_events(st):
    evs = []

    def e(cap, attr, value, unit=None):
        d = {"component": "main", "capability": cap, "attribute": attr, "value": value}
        if unit:
            d["unit"] = unit
        evs.append(d)

    if st.get("co2") is not None:
        e("carbonDioxideMeasurement", "carbonDioxide", st["co2"], "ppm")
    if st.get("temp") is not None:
        e("temperatureMeasurement", "temperature", st["temp"], "C")
    if st.get("hum") is not None:
        e("relativeHumidityMeasurement", "humidity", st["hum"], "%")
    if st.get("pm2_5") is not None:
        e("dustSensor", "fineDustLevel", st["pm2_5"])
    if st.get("tvocs") is not None:
        # the device reports an index, but the capability only accepts "ppb" as unit
        e("tvocMeasurement", "tvocLevel", st["tvocs"], "ppb")
    if st.get("power"):
        e("switch", "switch", st["power"])
    if st.get("fan") is not None:
        try:
            e("fanSpeed", "fanSpeed", int(st["fan"]))
        except (TypeError, ValueError):
            pass
    if st.get("filterPct") is not None:
        e("filterState", "filterLifeRemaining", st["filterPct"], "%")
    # a new virtual device starts with a random °F range; pin it to Celsius
    e("temperatureMeasurement", "temperatureRange", {"minimum": -20, "maximum": 50, "step": 1}, "C")
    e("mode", "supportedModes", list(MODE_N2K.values()))
    if st.get("mode"):
        e("mode", "mode", st["mode"])
    if st.get("lamp"):
        e("rapidCooling", "rapidCooling", st["lamp"])
    if st.get("hexPct") is not None:
        e("ultravioletIndex", "ultravioletIndex", st["hexPct"])
    return evs


# ---------------------------------------------------------------- commands
def st_status(dev):
    d = api("GET", "/devices/%s/status" % dev)
    if not d:
        return {}
    return status_from_api(d)


def status_from_api(d):
    try:
        m = d["components"]["main"]
        return {"power": m["switch"]["switch"]["value"],
                "fan": m["fanSpeed"]["fanSpeed"]["value"],
                "light": m["rapidCooling"]["rapidCooling"]["value"],
                "mode": m["mode"]["mode"]["value"]}
    except (KeyError, TypeError):
        return {}


# SmartThings attribute -> key in status_from_api() output
SELF_KEYS = {"switch": "power", "fanSpeed": "fan", "rapidCooling": "light", "mode": "mode"}


def user_commands(cur, prev, self_set):
    """Desired 'sensor' fragments for values the user changed in SmartThings.

    A value fires only if it differs from the previous poll AND from the last
    value the bridge itself pushed (otherwise our own telemetry echo would bounce
    back to the device as a command).
    """
    out = []
    if cur.get("power") and cur["power"] != prev.get("power") and cur["power"] != self_set.get("power"):
        out.append({"power": {"status": cur["power"]}})
    if cur.get("light") and cur["light"] != prev.get("light") and cur["light"] != self_set.get("light"):
        out.append({"lamp": {"style": "all" if cur["light"] == "on" else "none"}})
    if (cur.get("fan") is not None and str(cur["fan"]) != str(prev.get("fan"))
            and str(cur["fan"]) != str(self_set.get("fan"))):
        out.append({"fan": {"fanSpeed": str(cur["fan"])}})
    if (cur.get("mode") in MODE_K2N and cur["mode"] != prev.get("mode")
            and cur["mode"] != self_set.get("mode")):
        out.append({"mode": MODE_K2N[cur["mode"]]})
    return out


def _write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_cmd(sensor, path=None):
    """Merge into a still-pending cmd.json so two quick taps are both delivered."""
    path = path or CMD
    try:
        with open(path, encoding="utf-8") as f:
            pend = json.load(f).get("desired", {}).get("sensor", {})
    except (OSError, ValueError, AttributeError):
        pend = {}
    pend.update(sensor)
    _write_atomic(path, json.dumps({"desired": {"sensor": pend}}))


# ---------------------------------------------------------------- main loop
def main():
    dev = os.environ.get("ST_DEVICE_ID")
    if not dev:
        raise SystemExit("ST_DEVICE_ID is required")
    tele_every = float(os.environ.get("TELE_EVERY", "10"))
    cmd_poll = float(os.environ.get("CMD_POLL", "1"))
    log("bridge start device=%s data=%s" % (dev, DATA_DIR))

    last_push, self_set, prev, st = {}, {}, None, {}
    tick, tele_ticks = 0, max(1, int(tele_every / cmd_poll))
    while True:
        try:
            if tick % tele_ticks == 0:
                st = tail_sensor_state()
                evs = [e for e in to_events(st) if last_push.get(e["attribute"]) != e["value"]]
                if evs:
                    push_events(dev, evs)
                    for e in evs:
                        last_push[e["attribute"]] = e["value"]
                        if e["attribute"] in SELF_KEYS:
                            self_set[SELF_KEYS[e["attribute"]]] = e["value"]
                    log("pushed %d events co2=%s power=%s fan=%s lamp=%s mode=%s" % (
                        len(evs), st.get("co2"), st.get("power"), st.get("fan"), st.get("lamp"), st.get("mode")))
            tick += 1

            cur = st_status(dev)
            if cur:
                if prev is not None:  # never fire on the first poll
                    for frag in user_commands(cur, prev, self_set):
                        write_cmd(frag)
                        log("ST -> device %s" % json.dumps(frag))
                prev = cur
        except Exception as e:
            log("loop err", repr(e)[:120])
        time.sleep(cmd_poll)


if __name__ == "__main__":
    main()
