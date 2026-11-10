"""OAuth-free LAN adapter. Default read-only; no SmartThings credentials used."""
import argparse
import fcntl
import hmac
import json
import math
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEVICE_ID = os.environ.get('LXVENT_DEVICE_ID', 'lxvent-main')
MODES = {'2': 'auto', '3': 'turbo', '4': 'sleep', '5': 'manual'}

def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def parse_state(raw):
    state = {}
    for line in raw.splitlines():
        try:
            obj = json.loads(line.split(' | ', 1)[1])
            if not isinstance(obj, dict): continue
            sensor = obj.get('sensor', {})
            if not isinstance(sensor, dict): continue
            ac = sensor.get('airCondition', {})
            if isinstance(ac, dict):
                for src, dst in [('co2','co2'),('pm2_5','pm25'),('tvocs','tvoc'),('temperature','temperature'),('humidity','humidity')]:
                    v = ac.get(src)
                    if number(v): state[dst] = v
            for ss in (obj.get('sensorStatus', {}), sensor):
                if not isinstance(ss, dict): continue
                pw = ss.get('power', {})
                if isinstance(pw, dict) and pw.get('status') in ('on','off'): state['power'] = pw['status']
                fan = ss.get('fan', {})
                if isinstance(fan, dict) and str(fan.get('fanSpeed')) in ('0','1','2','3'): state['fan'] = int(fan['fanSpeed'])
                lamp = ss.get('lamp', {})
                if isinstance(lamp, dict) and lamp.get('style') in ('all','none'): state['lamp'] = 'on' if lamp['style'] == 'all' else 'off'
                mode = ss.get('mode')
                if isinstance(mode, dict): mode = mode.get('currentMode')
                if str(mode) in MODES: state['mode'] = MODES[str(mode)]
            for key, dst, life, usage in [('filter','filterPct','filterLifeTime','filterUsageTime'),('heatExchanger','hexPct','lifeTime','usageTime')]:
                v = sensor.get(key, obj.get(key, {}))
                if not isinstance(v, dict): continue
                total, used = v.get(life, v.get('lifeTime')), v.get(usage, v.get('usageTime'))
                if number(total) and total > 0 and number(used): state[dst] = max(0, min(100, round(100 - used * 100 / total)))
        except (ValueError, IndexError, TypeError):
            continue
    return state

def command_fragment(body):
    if not isinstance(body, dict) or set(body) != {'command','value'}: raise ValueError('expected command and value')
    name, v = body['command'], body['value']
    if name == 'power' and v in ('on','off'): return {'power': {'status': v}}
    if name == 'lamp' and v in ('on','off'): return {'lamp': {'style': 'all' if v == 'on' else 'none'}}
    if name == 'mode' and v in MODES.values(): return {'mode': next(k for k, value in MODES.items() if v == value)}
    if name == 'fan' and type(v) is int and 1 <= v <= 3: return {'mode':'5', 'fan': {'fanSpeed': str(v)}}
    raise ValueError('unsupported command or value')

class Adapter:
    def __init__(self, directory, writable=False, clock=time.monotonic):
        self.directory, self.writable, self.clock = Path(directory), writable, clock
        self.lock = threading.Lock()
        self.pending = {}
        self.expires = 0
        self.settle_until = 0
        self.next_write_at = 0
        self.closed = threading.Event()
        self.error = None
        if writable:
            self.owner = open(self.directory / '.edge-writer.lock', 'a')
            try:
                fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.owner.close()
                raise
    def state(self):
        path = self.directory / 'raw.log'
        try:
            with path.open('rb') as f:
                stat = os.fstat(f.fileno())
                f.seek(max(0, stat.st_size - 1024 * 1024))
                raw = f.read().decode('utf-8', 'replace')
            age = max(0, time.time() - stat.st_mtime)
            state = parse_state(raw)
            return {'id': DEVICE_ID, 'online': age < 600 and bool(state), 'age': round(age, 1), 'writable': self.writable, 'state': state}
        except OSError:
            return {'id': DEVICE_ID, 'online': False, 'writable': self.writable, 'state': {}}
    def submit(self, body):
        fragment = command_fragment(body)
        if not self.writable: raise PermissionError('read-only adapter')
        with self.lock:
            if fragment.get('power', {}).get('status') == 'off':
                self.pending.clear()  # Never execute deferred mode/fan after a later OFF.
                self.settle_until = 0
            if body['command'] == 'mode':
                self.pending.pop('fan', None)
            self.expires = self.clock() + 30
            self.pending.update(fragment)  # bounded, latest value wins
    def tick(self):
        with self.lock:
            path = self.directory / 'cmd.json'
            if self.clock() > self.expires:
                self.pending.clear()
            if path.exists(): return
            now = self.clock()
            if self.settle_until == float('inf'):
                self.settle_until = now + 4
            if self.next_write_at == float('inf'):
                self.next_write_at = now + 1
            if not self.pending: return
            if 'power' in self.pending:
                fragment = {'power': self.pending['power']}
            elif now >= self.settle_until and now >= self.next_write_at:
                # Firmware processes only one sensor command per desired envelope.
                key = next(k for k in ('mode', 'fan', 'lamp') if k in self.pending)
                if key == 'fan' and self.state()['state'].get('mode') != 'manual':
                    return  # Wait for the preceding manual-mode report, bounded by queue TTL.
                fragment = {key: self.pending[key]}
            else: return
            # Existing emulator is not lock-aware: only ONE command producer may own cmd.json.
            tmp = self.directory / '.edge-cmd.tmp'
            with tmp.open('w') as f:
                json.dump({'desired': {'sensor': fragment}}, f)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, path)
            for key in fragment: self.pending.pop(key, None)
            self.next_write_at = float('inf')
            if fragment.get('power', {}).get('status') == 'on':
                # Wait for emulator consumption, then four full seconds before dependent writes.
                self.settle_until = float('inf')
            self.error = None
    def run(self):
        while not self.closed.wait(0.1):
            try:
                self.tick()
            except OSError:
                self.error = 'command write failed'  # no credentials or payload in logs

def make_handler(adapter, secret):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status); self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def authorized(self):
            return hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer ' + secret)
        def do_GET(self):
            if not self.authorized(): return self.reply(401, {'error':'unauthorized'})
            if self.path != '/v1/state': return self.reply(404, {'error':'not found'})
            self.reply(200, adapter.state())
        def do_POST(self):
            if not self.authorized(): return self.reply(401, {'error':'unauthorized'})
            if self.path != '/v1/command': return self.reply(404, {'error':'not found'})
            try:
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 1024: return self.reply(413, {'error':'invalid body size'})
                body = json.loads(self.rfile.read(size))
                adapter.submit(body)
                self.reply(202, {'accepted':True})  # Acceptance is NOT physical acknowledgement.
            except PermissionError: self.reply(403, {'error':'read-only adapter'})
            except (ValueError, TypeError): self.reply(400, {'error':'invalid command'})
        def setup(self):
            super().setup(); self.connection.settimeout(5)
    return Handler

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--key-file', required=True)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--enable-commands', action='store_true', default=os.environ.get('LXVENT_ENABLE_COMMANDS', 'false').lower() == 'true')
    args = parser.parse_args()
    secret = Path(args.key_file).read_text().strip()
    if not DEVICE_ID: parser.error('LXVENT_DEVICE_ID must not be empty')
    if len(secret) < 32 or secret == '0' * 32: parser.error('LAN key must contain at least 32 characters')
    adapter = Adapter(args.data_dir, args.enable_commands)
    worker = threading.Thread(target=adapter.run, daemon=True); worker.start()
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(adapter, secret))
    try: server.serve_forever()
    finally: adapter.closed.set(); server.server_close(); worker.join()

if __name__ == '__main__': main()
