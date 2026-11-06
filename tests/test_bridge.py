import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bridge"))
sys.path.insert(0, os.path.join(ROOT, "provisioning"))

import bridge  # noqa: E402
import provision  # noqa: E402

T = "devices/test-device/messages/events/"
SAMPLE = [
    T + ' | {"sensorStatus":{"lamp":{"style":"none"},"fan":{"fanSpeed":"0"},"mode":"0","timer":{"status":"off"}}}',
    T + ' | {"sensor":{"airCondition":{"co2":812,"pm2_5":9,"tvocs":150,"temperature":23,"humidity":41}}}',
    T + ' | {"sensor":{"power":{"status":"on"}}}',
    T + ' | {"sensor":{"filter":{"filterLifeTime":2000,"filterUsageTime":333,'
        '"cleanLifeTime":500,"cleanUsageTime":600},"heatExchanger":{"lifeTime":1000,"usageTime":1}}}',
    T + ' | {"operation":"updated","sensor":{"lamp":{"style":"all"}}}',
    T + ' | {"operation":"updated","sensor":{"mode":{"currentMode":3}}}',
    T + ' | {"operation":"updated","sensor":{"fan":{"fanSpeed":"2"}}}',
    "garbage line without json",
    T + " | {not json}",
]


def lines():
    return ["12:00:%02d %s" % (i, ln) for i, ln in enumerate(SAMPLE)]


class ParseTest(unittest.TestCase):
    def test_parse_lines(self):
        st = bridge.parse_lines(lines())
        self.assertEqual(st["co2"], 812)
        self.assertEqual(st["pm2_5"], 9)
        self.assertEqual(st["tvocs"], 150)
        self.assertEqual(st["temp"], 23)
        self.assertEqual(st["hum"], 41)
        self.assertEqual(st["power"], "on")
        self.assertEqual(st["lamp"], "on")       # echo "all" overrides earlier "none"
        self.assertEqual(st["mode"], "turbo")    # currentMode 3
        self.assertEqual(st["fan"], "2")
        self.assertEqual(st["filterPct"], 83)    # floor(100 - 16.65)
        self.assertEqual(st["cleanPct"], 0)      # over 100% used -> clamp 0
        self.assertEqual(st["hexPct"], 99)       # floor(99.9)

    def test_mode_off_keeps_last_mode(self):
        st = bridge.parse_lines([
            "t " + T + ' | {"sensorStatus":{"mode":"4"}}',
            "t " + T + ' | {"sensorStatus":{"mode":"0"}}',
            "t " + T + ' | {"sensorStatus":{"mode":"5"}}',
        ])
        self.assertEqual(st["mode"], "sleep")

    def test_tail_reads_only_last_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "raw.log")
            with open(p, "w") as f:
                f.write("t %s | {\"sensor\":{\"airCondition\":{\"co2\":400}}}\n" % T)
                f.write("x" * 5000 + "\n")
                f.write("t %s | {\"sensor\":{\"airCondition\":{\"co2\":999}}}\n" % T)
            self.assertEqual(bridge.parse_lines(bridge.read_tail_lines(p, 200))["co2"], 999)
            self.assertEqual(bridge.read_tail_lines(p, 200)[0][:1], "t")  # partial line dropped
            self.assertEqual(bridge.read_tail_lines(os.path.join(d, "missing"), 200), [])


class EventsTest(unittest.TestCase):
    def test_to_events(self):
        evs = {e["attribute"]: e for e in bridge.to_events(bridge.parse_lines(lines()))}
        self.assertEqual(evs["carbonDioxide"]["value"], 812)
        self.assertEqual(evs["carbonDioxide"]["unit"], "ppm")
        self.assertEqual(evs["fanSpeed"]["value"], 2)
        self.assertEqual(evs["rapidCooling"]["value"], "on")
        self.assertEqual(evs["mode"]["value"], "turbo")
        self.assertEqual(evs["supportedModes"]["value"], ["auto", "turbo", "sleep"])
        self.assertEqual(evs["temperatureRange"]["unit"], "C")
        self.assertEqual(evs["filterLifeRemaining"]["value"], 83)
        self.assertEqual(evs["ultravioletIndex"]["value"], 99)
        self.assertEqual(evs["tvocLevel"]["value"], 150)
        self.assertTrue(all(e["component"] == "main" for e in evs.values()))

    def test_empty_state_still_pins_range(self):
        attrs = [e["attribute"] for e in bridge.to_events({})]
        self.assertEqual(attrs, ["temperatureRange", "supportedModes"])


class CommandTest(unittest.TestCase):
    PREV = {"power": "off", "fan": 1, "light": "off", "mode": "auto"}

    def test_user_changes(self):
        cur = {"power": "on", "fan": 3, "light": "on", "mode": "sleep"}
        self.assertEqual(bridge.user_commands(cur, self.PREV, {}), [
            {"power": {"status": "on"}},
            {"lamp": {"style": "all"}},
            {"fan": {"fanSpeed": "3"}},
            {"mode": "4"},
        ])

    def test_no_change_no_command(self):
        self.assertEqual(bridge.user_commands(dict(self.PREV), self.PREV, {}), [])

    def test_echo_guard(self):
        # values the bridge itself pushed must not bounce back as commands
        cur = {"power": "on", "fan": 2, "light": "on", "mode": "turbo"}
        self_set = {"power": "on", "fan": 2, "light": "on", "mode": "turbo"}
        self.assertEqual(bridge.user_commands(cur, self.PREV, self_set), [])

    def test_lamp_off_and_unknown_mode(self):
        prev = dict(self.PREV, light="on")
        cur = dict(self.PREV, light="off", mode="weird")
        self.assertEqual(bridge.user_commands(cur, prev, {}), [{"lamp": {"style": "none"}}])

    def test_status_from_api(self):
        d = {"components": {"main": {
            "switch": {"switch": {"value": "on"}}, "fanSpeed": {"fanSpeed": {"value": 2}},
            "rapidCooling": {"rapidCooling": {"value": "off"}}, "mode": {"mode": {"value": "auto"}}}}}
        self.assertEqual(bridge.status_from_api(d), {"power": "on", "fan": 2, "light": "off", "mode": "auto"})
        self.assertEqual(bridge.status_from_api({}), {})

    def test_write_cmd_merges(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "cmd.json")
            bridge.write_cmd({"power": {"status": "on"}}, p)
            bridge.write_cmd({"mode": "3"}, p)
            with open(p) as f:
                self.assertEqual(json.load(f), {"desired": {"sensor": {"power": {"status": "on"}, "mode": "3"}}})
            self.assertFalse(os.path.exists(p + ".tmp"))


class ProvisionFrameTest(unittest.TestCase):
    def test_roundtrip(self):
        msg = {"type": "REQ", "code": "LG100"}
        frame = provision.encode_frame(msg)
        self.assertEqual(frame[:8], b"LGHAUSYS")
        self.assertEqual(int.from_bytes(frame[8:12], "big"), len(frame) - 12)
        self.assertEqual(provision.decode_frame(frame), msg)

    def test_messages_and_masking(self):
        a = provision.parse_args(["--ssid", "Home", "--password", "pw123456",
                                  "--dps-endpoint", "vent.example.com", "--device-id", "dev-1"])
        m = provision.build_messages(a)
        self.assertEqual([x["code"] for x in m], ["LG100", "LG200", "LG300"])
        self.assertEqual(m[1]["deviceId"], ["dev-1"])
        self.assertEqual(m[1]["dpsEndpoint"], "vent.example.com")
        shown = json.dumps(provision.masked(m[1]))
        self.assertNotIn("pw123456", shown)
        self.assertNotIn(a.dps_key, shown)


if __name__ == "__main__":
    unittest.main()
