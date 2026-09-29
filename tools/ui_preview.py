"""Isolated UI preview using synthetic data; never probes hosts or runs controls."""
import argparse
import json
import math
import mimetypes
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
from urllib.parse import parse_qs, urlsplit, unquote
ROOT = Path(__file__).resolve().parents[1]


def sample_state(fault=False):
    now = time.time()
    machines = []
    for index, name in enumerate(('Demo compute', 'Demo storage')):
        down = fault and index == 1
        cpu = [12 + index * 8 + math.sin(n / 5) * 9 for n in range(60)]
        machines.append({'id': 'compute' if index == 0 else 'storage', 'name': name, 'ip': f'192.0.2.{10+index}',
            'role': 'Example host', 'os': 'Linux', 'platform': 'linux', 'port': 22,
            'up': not down, 'since': now-120, 'latency_ms': None if down else 1.2,
            'error': 'Connection timed out' if down else None, 'history': '1'*70+('0'*20 if down else '1'*20),
            'latency_history': [1.2 + abs(math.sin(n)) for n in range(90)], 'wakeable': False, 'power': False,
            'telemetry': {'updated': now-120 if down else now, 'stale': down, 'cpu': cpu[-1], 'cpu_history': cpu,
                'cores':4, 'load':[.4,.3,.2], 'mem_used':3*2**30, 'mem_total':8*2**30,
                'disk_used':95*2**30 if fault else 30*2**30, 'disk_total':100*2**30,
                'temp':42, 'uptime':20000, 'link_mbps':1000, 'iface':'eth0', 'rx_rate':120000, 'tx_rate':30000,
                'rx_history':[120000]*60, 'tx_history':[30000]*60}})
    services = [{'id':'demo-service','name':'Demo web service','host':'compute','description':'Preview example',
                 'endpoint':'192.0.2.10:8080','probe_path':'/health','open':'','up':True,'code':200,'history':'1'*90,
                 'latency_ms':2.4,'latency_history':[2.4]*90}]
    return {'version':'0.1.0', 'generated':now,'updated':now,'started':now-3600,'interval':10,'history_len':90,
            'machines':machines,'services':services,'branding':{'name':'Annunciator demo','subtitle':'PREVIEW DATA','accent':'#dc6b2f'},
            'controls':False,'containers':{},'speedtest':{'running':False,'routes':[]},
            'events':[{'id':int(now*1000),'ts':now-120,'kind':'machine','target':'machine:storage',
                       'severity':'crit' if fault else 'ok','title':'Demo storage offline' if fault else 'Demo storage online','text':'Preview event'}],
            'thresholds':{'disk_warn':.8,'disk_crit':.9,'mem_warn':.9}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=18162)
    parser.add_argument('--scenario',choices=('healthy','fault','offline','first-run'),default='healthy')
    parser.add_argument('--safe-top',type=int,choices=(0,24,32,48),default=0)
    args=parser.parse_args()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed=urlsplit(self.path)
            if parsed.path=='/api/state':
                if args.scenario=='offline': self.send(503,b'{"error":"Preview offline"}','application/json'); return
                self.send(200,json.dumps(sample_state(args.scenario=='fault')).encode(),'application/json'); return
            target=(ROOT/'web'/unquote(parsed.path.lstrip('/') or 'index.html')).resolve()
            if ROOT/'web' not in target.parents or not target.is_file(): self.send(404,b'not found','text/plain'); return
            body=target.read_bytes()
            if target.name=='index.html':
                query=parse_qs(parsed.query)
                safe=query.get('safe_top',[str(args.safe_top)])[0]
                if safe not in ('0','24','32','48'): safe='0'
                native=''
                if args.scenario=='first-run':
                    native="<script>window.Capacitor={isNativePlatform:()=>true,Plugins:{Updater:{configureAlerts:()=>Promise.resolve(),version:()=>Promise.resolve({versionName:'preview',versionCode:1}),minimize:()=>Promise.resolve()},SystemBars:{setStyle:()=>Promise.resolve()}}};</script>"
                extra=f'<style>:root{{--safe-top:{safe}px !important;}}body::after{{display:block !important;content:"DEMO DATA — PREVIEW ONLY";position:fixed;right:8px;bottom:90px;font:10px monospace;color:#dc6b2f;pointer-events:none;z-index:100;inset:auto;width:auto;height:auto;}}</style>'+native
                if query.get('reduced')==['1']:
                    extra+='<style>*,*::before,*::after{animation:none !important;transition:none !important;}</style>'
                body=body.replace(b'</head>',extra.encode()+b'</head>')
            if target.name=='sw.js': body=body.replace(b'__APP_VERSION__',b'preview')
            self.send(200,body,mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        def do_POST(self): self.send(403,b'{"ok":false,"error":"Preview never executes controls"}','application/json')
        def send(self,code,body,ctype):
            self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass
    print(f'Preview only: http://127.0.0.1:{args.port}, {args.scenario}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()

if __name__=='__main__': main()
