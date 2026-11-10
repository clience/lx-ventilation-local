"""Optional standalone local alerts; no ST API. Dry-run unless --send is supplied."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request
from server import Adapter, number

ALLOWED = {'filterPct', 'hexPct', 'co2'}

def evaluate(rules, state, sent, deliver):
    result = dict(sent)
    for rule in rules:
        if rule.get('vent') not in ALLOWED or 'device' in rule:
            raise ValueError('Only local CO2/filter/heat exchanger rules are supported')
        value = state.get(rule['vent'])
        if not number(value): continue
        rid = rule['id']
        if 'above' in rule:
            hit, back = value >= rule['above'], value <= rule.get('rearm',rule['above'])
        else:
            hit, back = value <= rule['below'], value >= rule.get('rearm',rule['below'])
        if hit and not result.get(rid):
            text = '🔔 %s %s%s — %s' % (rule.get('name',rid),value,rule.get('unit',''),rule.get('msg',''))
            if deliver(text): result[rid] = True
        elif back and not hit: result[rid] = False
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--data-dir',required=True)
    p.add_argument('--rules',required=True)
    p.add_argument('--state-file',required=True)
    p.add_argument('--telegram-token-file')
    p.add_argument('--chat-id')
    p.add_argument('--send',action='store_true',default=os.environ.get('LXVENT_ALERT_SEND','false').lower() == 'true')
    p.add_argument('--health-file')
    args=p.parse_args()
    if args.send and (not args.telegram_token_file or not args.chat_id): p.error('--send requires Telegram token file and chat id')
    rules=json.loads(Path(args.rules).read_text())
    if any(r.get('vent') not in ALLOWED or 'device' in r for r in rules): p.error('Unsupported alert rule')
    adapter=Adapter(args.data_dir)
    state_path=Path(args.state_file)
    if args.send:
        owner=open(str(state_path)+'.lock','a')
        fcntl.flock(owner,fcntl.LOCK_EX | fcntl.LOCK_NB)
    sent=json.loads(state_path.read_text()) if state_path.exists() else {}
    def deliver(text):
        if not args.send: return False
        try:
            token=Path(args.telegram_token_file).read_text().strip()
            data=urllib.parse.urlencode({'chat_id':args.chat_id,'text':text}).encode()
            with urllib.request.urlopen('https://api.telegram.org/bot'+token+'/sendMessage',data=data,timeout=10) as response:
                return json.load(response).get('ok') is True
        except Exception: return False  # never log token-bearing exception URLs
    while True:
        snapshot=adapter.state()
        if snapshot['online']:
            updated=evaluate(rules,snapshot['state'],sent,deliver)
            if args.send and updated != sent:
                tmp=state_path.with_suffix('.tmp')
                tmp.write_text(json.dumps(updated)); os.replace(tmp,state_path)
            sent=updated
        if args.health_file:
            health=Path(args.health_file)
            tmp=health.with_suffix('.tmp')
            tmp.write_text(json.dumps({'checked_at':time.time(),'source_online':snapshot['online'],'rules':len(rules),'sending_enabled':args.send}))
            os.replace(tmp,health)
        time.sleep(10)

if __name__=='__main__': main()
