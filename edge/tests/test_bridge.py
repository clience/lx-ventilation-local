import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

spec = importlib.util.spec_from_file_location('server', Path(__file__).parents[1]/'bridge/server.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)
        self.now = 100
        self.a = m.Adapter(self.path, True, lambda:self.now)
    def tearDown(self):
        self.a.owner.close(); self.tmp.cleanup()
    def send(self, command, value): self.a.submit({'command':command,'value':value})
    def take(self):
        p=self.path/'cmd.json'; result=json.loads(p.read_text())['desired']['sensor']; p.unlink(); return result
    def test_power_settle_begins_after_consumption(self):
        self.send('power','on'); self.send('mode','sleep'); self.a.tick()
        self.assertEqual(self.take(), {'power':{'status':'on'}})
        self.a.tick(); self.assertFalse((self.path/'cmd.json').exists())
        # The worker detects consumption, then starts settling.
        self.a.settle_until=self.now+4
        self.now+=3.99; self.a.tick(); self.assertFalse((self.path/'cmd.json').exists())
        self.now+=0.01; self.a.tick(); self.assertEqual(self.take(), {'mode':'4'})
    def test_off_cancels_delayed_controls(self):
        self.send('power','on'); self.a.tick(); self.take()
        self.send('mode','turbo'); self.send('power','off'); self.a.tick()
        self.assertEqual(self.take(), {'power':{'status':'off'}})
        self.now+=10; self.a.tick(); self.assertFalse((self.path/'cmd.json').exists())
    def test_pending_mailbox_never_overwritten(self):
        (self.path/'cmd.json').write_text('{"existing":true}')
        self.send('fan',2); self.a.tick()
        self.assertEqual(json.loads((self.path/'cmd.json').read_text()),{'existing':True})
        (self.path/'cmd.json').unlink(); self.a.tick()
        self.assertEqual(self.take(), {'mode':'5'})
        self.a.tick(); self.now+=1
        self.a.tick(); self.assertFalse((self.path/'cmd.json').exists())
        (self.path/'raw.log').write_text('00:00:01 topic | {"sensor":{"mode":"5"}}\n')
        self.a.tick(); self.assertEqual(self.take(), {'fan':{'fanSpeed':'2'}})
    def test_explicit_mode_cancels_queued_fan(self):
        self.send('fan',3); self.send('mode','auto'); self.a.tick()
        self.assertEqual(self.take(), {'mode':'2'})
        self.a.tick(); self.now+=2; self.a.tick()
        self.assertFalse((self.path/'cmd.json').exists())
    def test_multiple_controls_are_separate_envelopes(self):
        self.send('mode','sleep'); self.send('lamp','on'); self.a.tick()
        self.assertEqual(self.take(), {'mode':'4'})
        self.a.tick(); self.now+=1; self.a.tick()
        self.assertEqual(self.take(), {'lamp':{'style':'all'}})
    def test_latest_command_wins(self):
        self.send('mode','turbo'); self.send('mode','sleep'); self.a.tick()
        self.assertEqual(self.take(), {'mode':'4'})
    def test_queue_expires(self):
        self.send('mode','turbo'); self.now+=31; self.a.tick()
        self.assertFalse((self.path/'cmd.json').exists())
    def test_worker_consumption_and_delay(self):
        self.send('power','on'); self.send('mode','sleep')
        worker=threading.Thread(target=self.a.run); worker.start()
        try:
            deadline=time.monotonic()+2
            while not (self.path/'cmd.json').exists() and time.monotonic()<deadline: time.sleep(.02)
            self.assertEqual(self.take(), {'power':{'status':'on'}})
            time.sleep(.2)
            self.assertEqual(self.a.settle_until,104)
            self.now=103.9; time.sleep(.2); self.assertFalse((self.path/'cmd.json').exists())
            self.now=104; time.sleep(.2); self.assertEqual(self.take(),{'mode':'4'})
        finally: self.a.closed.set(); worker.join()
    def test_validation(self):
        for body in [{},[],{'command':'mode','value':'exhaust'},{'command':'fan','value':True},{'command':'fan','value':4},{'command':'fan','value':'2'},{'command':'lamp','value':'all'}]:
            with self.assertRaises(ValueError): m.command_fragment(body)
    def test_readonly(self):
        a=m.Adapter(self.path)
        with self.assertRaises(PermissionError): a.submit({'command':'power','value':'on'})
    def test_single_writer(self):
        with self.assertRaises(BlockingIOError): m.Adapter(self.path,True)
    def test_parser(self):
        objects=[{'sensor':{'airCondition':{'co2':900,'temperature':22,'humidity':58,'tvocs':105,'pm2_5':20},'filter':{'filterLifeTime':4000,'filterUsageTime':2249},'heatExchanger':{'lifeTime':20000,'usageTime':2249}}}, {'sensorStatus':{'mode':4,'fan':{'fanSpeed':'2'},'lamp':{'style':'all'}}}, {'sensor':{'power':{'status':'off'},'mode':'0'}}]
        raw='garbage\n'+ '\n'.join('00:00:00 topic | '+json.dumps(o) for o in objects)+'\npartial | {'
        s=m.parse_state(raw)
        self.assertEqual(s,{'co2':900,'temperature':22,'humidity':58,'tvoc':105,'pm25':20,'filterPct':44,'hexPct':89,'mode':'sleep','fan':2,'lamp':'on','power':'off'})
    def test_stale_missing_and_rotation(self):
        self.assertFalse(self.a.state()['online'])
        p=self.path/'raw.log'; p.write_text('00:00:00 topic | {"sensor":{"power":{"status":"on"}}}\n')
        self.assertTrue(self.a.state()['online'])
        import os
        os.utime(p,(time.time()-601,time.time()-601)); self.assertFalse(self.a.state()['online'])
        p.write_text('00:00:01 topic | {"sensor":{"power":{"status":"off"}}}\n')
        self.assertEqual(self.a.state()['state']['power'],'off')
    def test_http_auth_and_readonly(self):
        key='test-key-'*5
        server=ThreadingHTTPServer(('127.0.0.1',0),m.make_handler(m.Adapter(self.path),key))
        thread=threading.Thread(target=server.serve_forever); thread.start()
        url='http://127.0.0.1:'+str(server.server_port)
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm: urllib.request.urlopen(url+'/v1/state')
            self.assertEqual(cm.exception.code,401); cm.exception.close()
            req=urllib.request.Request(url+'/v1/state',headers={'Authorization':'Bearer '+key})
            with urllib.request.urlopen(req) as r: self.assertEqual(json.load(r)['id'],m.DEVICE_ID)
            req=urllib.request.Request(url+'/v1/command',data=b'{"command":"power","value":"on"}',headers={'Authorization':'Bearer '+key})
            with self.assertRaises(urllib.error.HTTPError) as cm: urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code,403); cm.exception.close()
        finally: server.shutdown(); server.server_close(); thread.join()

if __name__=='__main__': unittest.main()
