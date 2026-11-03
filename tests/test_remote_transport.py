"""Exercise the real hc subprocess adapter and HTTP endpoints using a local transport fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from kanban.collector import Collector
from kanban.installation import write_registration
from kanban.remote import HcTransport, RemoteManager
from kanban.server import handler
from kanban.store import ROOT, Store
from kanban.sync import enable_export
from tests.test_remote_sync import session


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = Store(self.root/'source.sqlite3')
        self.main = Store(self.root/'main.sqlite3')
        self.registry = self.root/'registry'
        self.device = dict(mode='collector', root=str(ROOT), uid=os.getuid(),
                           python=sys.executable, db=str(self.source.path), id='a'*32, alias='home-macStudio')
        write_registration(self.device, self.registry)
        self.endpoint = self.root/'Application Support/remote'
        self.endpoint.parent.mkdir()
        self.endpoint.write_text('#!' + sys.executable + '\n' + f'''
import sys,os
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
from kanban import installation
installation.SYSTEM = Path({str(self.registry)!r})
os.environ['KANBAN_DB'] = {str(self.source.path)!r}
from kanban.remote_rpc import main
main()
''')
        self.endpoint.chmod(0o700)
        endpoint_patch = patch('kanban.remote.ENDPOINT', str(self.endpoint))
        endpoint_patch.start()
        self.addCleanup(endpoint_patch.stop)
        self.hc = self.root/'fake hc'
        self.hc.write_text('#!' + sys.executable + '\n' + f'''
import json,sys,os,subprocess
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
if sys.argv[1:] == ['machine','ls','--json']:
    print(json.dumps({{'machines':[{{'name':'home','state':'connected'}}]}}))
else:
    assert sys.argv[1:] == ['exec','-i','--on','home','--','/bin/sh'], sys.argv
    # Reproduce hc's remote shell boundary instead of calling the RPC directly.
    sys.exit(subprocess.run(['/bin/sh','-c',' '.join(sys.argv[6:])]).returncode)
''')
        self.hc.chmod(0o700)
        self.transport = HcTransport(str(self.hc))
        self.manager = RemoteManager(self.main, self.transport)

    def test_real_subprocess_json_stdin_claim_and_incremental_collection(self):
        enable_export(self.source)
        codex = self.root/'codex'
        claude = self.root/'claude'
        codex.mkdir(); (claude/'project').mkdir(parents=True)
        at = '2026-11-03T00:00:00+00:00'
        events = [dict(timestamp=at, type='session_meta', payload=dict(id='codex-test',cwd='/project',source='cli')),
                  dict(timestamp=at, type='event_msg', payload={'type':'task_started'}),
                  dict(timestamp=at, type='response_item', payload=dict(type='message',role='user',content=[{'type':'input_text','text':'Codex 요청'}])),
                  dict(timestamp=at, type='event_msg', payload={'type':'task_complete','last_agent_message':'Codex 완료'})]
        (codex/'one.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
        ce = [dict(type='user',uuid='turn-1',timestamp=at,cwd='/project',sessionId='claude-test',message={'role':'user','content':'Claude 요청'}),
              dict(type='assistant',uuid='answer',timestamp=at,cwd='/project',sessionId='claude-test',message={'role':'assistant','content':[{'type':'text','text':'Claude 완료'}],'stop_reason':'end_turn'})]
        (claude/'project/one.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in ce))
        originals = {p:p.read_bytes() for p in (codex/'one.jsonl',claude/'project/one.jsonl')}
        collector = Collector(self.source, [('codex',codex),('claude',claude)])
        collector.scan()
        self.assertEqual(len(self.source.cards()), 2)
        info = self.manager.probe('home')
        self.assertEqual(info['alias'], 'home-macStudio')
        self.manager.connect('home', info['device_id'])
        while self.manager.sync_one(info['device_id']):
            pass
        self.assertEqual({c['provider'] for c in self.main.cards()}, {'codex','claude'})
        self.assertTrue(all(c['device_alias'] == 'home-macStudio' for c in self.main.cards()))
        for p, original in originals.items():
            self.assertEqual(p.read_bytes(), original)
        self.manager.disconnect(info['device_id'])
        self.assertFalse(self.manager.probe('home')['bound'])

    def test_space_in_endpoint_and_request_stays_data(self):
        marker = self.root/'must-not-exist'
        info = self.transport.rpc('home', {'op':'info', 'extra':
            '$(touch ' + str(marker) + ')\nKANBAN_REQUEST\ntouch ' + str(marker)})
        self.assertEqual(info['device_id'], self.device['id'])
        self.assertFalse(marker.exists())

    def test_unavailable_bad_json_timeout_and_output_limit(self):
        missing = HcTransport(str(self.hc))
        missing.executable = None
        with self.assertRaisesRegex(ValueError, 'hc'):
            missing.machines()
        for body, message in [('print("not-json")','응답'), ('import time; time.sleep(5)','시간'),
                              ('print("x"*4096)','너무 큽니다')]:
            self.hc.write_text('#!'+sys.executable+'\n'+body+'\n')
            with patch('kanban.remote.MAX_OUTPUT',1024), self.assertRaisesRegex(ValueError,message):
                self.transport._run([], timeout=.1 if message == '시간' else 2)

    def test_http_connect_pause_disconnect_and_no_secret_leak(self):
        session(self.source)
        server = ThreadingHTTPServer(('127.0.0.1',0),handler(self.main,remote=self.manager))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        def request(path='remotes', method='GET', value=None, extra=None):
            connection = HTTPConnection('127.0.0.1',server.server_address[1],timeout=5)
            headers = {'Content-Type':'application/json', **(extra or {})}
            connection.request(method,'/api/'+path,body=json.dumps(value) if value is not None else None,headers=headers)
            response = connection.getresponse()
            status, value = response.status, json.loads(response.read())
            connection.close()
            return status,value
        self.assertEqual(request('remotes/machines')[1]['machines'][0]['name'],'home')
        status,info = request(method='POST',value={'action':'probe','machine':'home'})
        self.assertEqual(status,200)
        status,value = request(method='POST',value={'action':'connect','machine':'home','device_id':info['device_id']})
        self.assertEqual(status,200)
        self.assertNotIn('token',json.dumps(value))
        self.assertNotIn(self.manager._device(info['device_id'])['token'],json.dumps(value))
        self.manager.sync_one(info['device_id'])
        self.assertEqual(request('state')[1]['cards'][0]['device_alias'],'home-macStudio')
        self.assertEqual(request(method='POST',value={'action':'pause','device_id':info['device_id'],'paused':True})[0],200)
        self.assertEqual(request(method='POST',value={'action':'disconnect','device_id':info['device_id']})[0],200)
        self.assertEqual(request(method='POST',value={'action':'probe','machine':'home'},extra={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(request(method='POST',value={'action':'probe','machine':'missing'})[0],400)
        self.assertEqual(len(self.main.cards()),1)


if __name__ == '__main__':
    unittest.main()
