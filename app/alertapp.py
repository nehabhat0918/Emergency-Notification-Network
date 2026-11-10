#!/usr/bin/env python3
"""AlertApp: send messages between machines over UDP, with a live traffic monitor.
Needs only Python 3. Run on every machine, then open the UI in a browser:
    python3 alertapp.py --name laptop1                 (UI: http://localhost:8080)
Two instances on one machine (testing):  --port 5000 --ui-port 8080   and   --port 6000 --ui-port 8081
Traffic classes: EMERGENCY (UDP port N, ACKed, retried), NORMAL (port N+2, ACKed), BULK (port N+2, no ACK).
Modes (how the send queue treats the classes): static = one FIFO | priority = EMERGENCY first | dynamic = priority + drops BULK when the queue builds up.
"""
import argparse
import json
import random
import socket
import statistics
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLASSES = ('EMERGENCY', 'NORMAL', 'BULK')
RETRIES = {'EMERGENCY': 4, 'NORMAL': 3}
TIMEOUT = 2.0
QCAP = 300
SHED_DEPTH = 60
MODES = ('static', 'priority', 'dynamic')


class Node:
    def __init__(self, a):
        self.name, self.port, self.ctl_ip = a.name, a.port, a.ctl_ip
        self.lock = threading.RLock()
        self.mode, self.link_kbps = 'priority', 1000
        self.peers = {}
        self.q = {c: deque() for c in CLASSES}
        self.pending = {}
        self.seen = deque(maxlen=500)
        self.stats = {c: dict(sent=0, acked=0, retries=0, failed=0, dropped=0, rx=0,
                              rtts=deque(maxlen=2000)) for c in CLASSES}
        self.buckets = {}
        self.feed = deque(maxlen=60)
        self.gen = dict(on=False, pps=200, size=1000)
        self.burst_open, self.last_emerg = False, 0.0
        self.socks = {}
        for p in (self.port, self.port + 2):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('0.0.0.0', p))
            s.settimeout(0.5)
            self.socks[p] = s
        for fn in (self.sender, self.retransmit, self.generator):
            threading.Thread(target=fn, daemon=True).start()
        for s in self.socks.values():
            threading.Thread(target=self.receiver, args=(s,), daemon=True).start()

    def log(self, kind, text):
        with self.lock:
            self.feed.append({'t': time.strftime('%H:%M:%S'), 'kind': kind, 'text': text})

    def bump(self, cls, idx, n):
        sec = int(time.time())
        with self.lock:
            b = self.buckets.setdefault(sec, {c: [0, 0] for c in CLASSES})
            b[cls][idx] += n
            for old in [k for k in self.buckets if k < sec - 70]:
                del self.buckets[old]

    def sock_for(self, cls):
        return self.socks[self.port if cls == 'EMERGENCY' else self.port + 2]

    def depth(self):
        return sum(len(d) for d in self.q.values())

    def send_ctl(self, kind):
        if self.ctl_ip:
            try:
                self.socks[self.port].sendto(json.dumps({'type': kind}).encode(), (self.ctl_ip, 9998))
            except OSError:
                pass

    def enqueue(self, it):
        cls = it['cls']
        with self.lock:
            it['qt'] = time.time()
            full = self.depth() >= QCAP
            if self.mode == 'dynamic' and cls == 'BULK' and self.depth() >= SHED_DEPTH:
                ok = False
            elif full and self.mode != 'static' and cls != 'BULK' and self.q['BULK']:
                self.q['BULK'].pop()
                self.stats['BULK']['dropped'] += 1
                ok = True
            else:
                ok = not full
            if not ok:
                self.stats[cls]['dropped'] += 1
                if cls in RETRIES and it['last_sent'] is None:
                    self.stats[cls]['failed'] += 1
                    self.pending.pop(it['id'], None)
                return False
            it['queued'] = True
            self.q[cls].append(it)
        if cls == 'EMERGENCY':
            self.last_emerg = time.time()
            if not self.burst_open:
                self.burst_open = True
                self.send_ctl('alert_start')
        return True

    def dequeue(self):
        with self.lock:
            if self.mode == 'static':
                best = None
                for c in CLASSES:
                    if self.q[c] and (best is None or self.q[c][0]['qt'] < self.q[best][0]['qt']):
                        best = c
            else:
                best = next((c for c in CLASSES if self.q[c]), None)
            if best is None:
                return None
            it = self.q[best].popleft()
            it['queued'] = False
            return it

    def submit(self, cls, peer_key, text='', size=0):
        it = {'id': uuid.uuid4().hex[:12], 'cls': cls, 'peer': peer_key, 'text': text, 'size': size,
              'enq': time.time(), 'qt': 0, 'tries': 0, 'first_sent': None, 'last_sent': None, 'queued': False}
        if cls in RETRIES:
            with self.lock:
                self.pending[it['id']] = it
        return self.enqueue(it)

    def sender(self):
        while True:
            it = self.dequeue()
            if it is None:
                time.sleep(0.002)
                continue
            with self.lock:
                peer = self.peers.get(it['peer'])
            if peer is None:
                self.pending.pop(it['id'], None)
                continue
            cls = it['cls']
            msg = {'t': 'MSG', 'id': it['id'], 'cls': cls, 'text': it['text'], 'from': self.name,
                   'ts': time.time()}
            if it['size']:
                msg['pad'] = 'x' * it['size']
            data = json.dumps(msg).encode()
            port = peer['port'] if cls == 'EMERGENCY' else peer['port'] + 2
            try:
                self.sock_for(cls).sendto(data, (peer['ip'], port))
            except OSError:
                pass
            now = time.time()
            with self.lock:
                if it['first_sent'] is None:
                    it['first_sent'] = now
                    self.stats[cls]['sent'] += 1
                it['last_sent'] = now
            self.bump(cls, 0, len(data))
            if self.link_kbps > 0:
                time.sleep(len(data) * 8 / (self.link_kbps * 1000.0))

    def retransmit(self):
        while True:
            time.sleep(0.1)
            now = time.time()
            with self.lock:
                for it in list(self.pending.values()):
                    if it['queued'] or it['last_sent'] is None or now - it['last_sent'] < TIMEOUT:
                        continue
                    st = self.stats[it['cls']]
                    if it['tries'] >= RETRIES[it['cls']]:
                        self.pending.pop(it['id'], None)
                        st['failed'] += 1
                    else:
                        it['tries'] += 1
                        st['retries'] += 1
                        self.enqueue(it)
            if self.burst_open and now - self.last_emerg > 2.0 and not any(
                    p['cls'] == 'EMERGENCY' for p in self.pending.values()):
                self.burst_open = False
                self.send_ctl('alert_end')

    def generator(self):
        while True:
            g = self.gen
            if not g['on']:
                time.sleep(0.1)
                continue
            with self.lock:
                keys = list(self.peers)
            for k in keys:
                self.submit('BULK', k, size=int(g['size']))
            time.sleep(1.0 / max(1, g['pps']))

    def add_peer(self, ip, port, name):
        key = f'{ip}:{port}'
        with self.lock:
            new = key not in self.peers
            self.peers[key] = {'name': name, 'ip': ip, 'port': port, 'seen': time.time()}
        if new:
            self.log('peer', f'{name} joined ({key})')
        return key

    def hello(self, ip, port, kind='HELLO'):
        try:
            self.socks[self.port].sendto(
                json.dumps({'t': kind, 'name': self.name, 'port': self.port}).encode(), (ip, port))
        except OSError as e:
            self.log('error', f'cannot reach {ip}:{port} ({e})')

    def receiver(self, sock):
        while True:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                m = json.loads(data)
            except ValueError:
                continue
            t = m.get('t')
            if t == 'MSG':
                cls = m.get('cls') if m.get('cls') in CLASSES else 'NORMAL'
                self.bump(cls, 1, len(data))
                dup = m.get('id') in self.seen
                with self.lock:
                    if not dup:
                        self.seen.append(m.get('id'))
                        self.stats[cls]['rx'] += 1
                if not dup and cls != 'BULK':
                    self.log(cls, f"{m.get('from', '?')}: {m.get('text', '')}")
                if cls in RETRIES:
                    try:
                        sock.sendto(json.dumps({'t': 'ACK', 'id': m.get('id')}).encode(), addr)
                    except OSError:
                        pass
            elif t == 'ACK':
                with self.lock:
                    it = self.pending.pop(m.get('id'), None)
                    if it:
                        st = self.stats[it['cls']]
                        st['acked'] += 1
                        st['rtts'].append((time.time() - it['enq']) * 1000)
            elif t in ('HELLO', 'HELLO_OK'):
                self.add_peer(addr[0], int(m.get('port', 5000)), m.get('name', addr[0]))
                if t == 'HELLO':
                    self.hello(addr[0], int(m.get('port', 5000)), 'HELLO_OK')
            elif t == 'BYE':
                with self.lock:
                    self.peers.pop(f"{addr[0]}:{m.get('port')}", None)

    def state(self):
        now = int(time.time())
        with self.lock:
            series = []
            for s in range(now - 59, now + 1):
                b = self.buckets.get(s)
                series.append({c: [round(b[c][0] * 8 / 1000, 1), round(b[c][1] * 8 / 1000, 1)] if b else [0, 0]
                               for c in CLASSES})
            stats = {}
            for c in CLASSES:
                s = self.stats[c]
                r = sorted(s['rtts'])
                stats[c] = {k: s[k] for k in ('sent', 'acked', 'retries', 'failed', 'dropped', 'rx')}
                stats[c]['avg'] = round(statistics.mean(r), 1) if r else None
                stats[c]['p95'] = round(r[max(0, int(len(r) * .95) - 1)], 1) if r else None
            return {'name': self.name, 'port': self.port, 'mode': self.mode, 'link': self.link_kbps,
                    'gen': dict(self.gen), 'peers': [dict(k=k, **p) for k, p in self.peers.items()],
                    'queue': {c: len(self.q[c]) for c in CLASSES}, 'series': series, 'stats': stats,
                    'feed': list(self.feed)[-40:]}

    def reset(self):
        with self.lock:
            for c in CLASSES:
                self.stats[c].update(sent=0, acked=0, retries=0, failed=0, dropped=0, rx=0)
                self.stats[c]['rtts'].clear()
                self.q[c].clear()
            self.pending.clear()
            self.feed.clear()

    def act(self, path, b):
        if path == '/api/send':
            text, cls = str(b.get('text', ''))[:500], b.get('cls', 'NORMAL')
            if cls not in ('EMERGENCY', 'NORMAL') or not text:
                return 'need text and class'
            with self.lock:
                keys = list(self.peers) if b.get('to') in (None, 'ALL') else [b['to']]
            if not keys:
                return 'no peers: join one first'
            n = sum(self.submit(cls, k, text) for k in keys)
            return f'queued {n} message(s)'
        if path == '/api/join':
            self.hello(str(b.get('ip', '')).strip(), int(b.get('port', 5000)))
            return 'hello sent'
        if path == '/api/leave':
            with self.lock:
                self.peers.pop(b.get('key'), None)
            return 'removed'
        if path == '/api/mode' and b.get('mode') in MODES:
            self.mode = b['mode']
            return 'mode ' + self.mode
        if path == '/api/link':
            self.link_kbps = max(0, int(float(b.get('kbps', 0))))
            return f'link {self.link_kbps} kbit/s'
        if path == '/api/gen':
            self.gen.update(on=bool(b.get('on')), pps=max(1, min(2000, int(float(b.get('pps', 200))))),
                            size=max(10, min(1400, int(float(b.get('size', 1000))))))
            return 'generator ' + ('on' if self.gen['on'] else 'off')
        if path == '/api/reset':
            self.reset()
            return 'stats cleared'
        return 'unknown action'


PAGE = r"""<!DOCTYPE html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>AlertApp</title><style>
:root{--bg:#f4f1ea;--c:#fffdf8;--ink:#1d1b16;--m:#6f6a5d;--l:#ddd6c6;--EM:#d9480f;--NO:#3b6fb6;--BU:#8a8574;--ok:#2f9e5a}
@media(prefers-color-scheme:dark){:root{--bg:#12110d;--c:#1d1b15;--ink:#f0ece0;--m:#9a947f;--l:#36331f;--EM:#ff8a4c;--NO:#6fa3ec;--BU:#a39d88;--ok:#5ccf84}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px system-ui,sans-serif;padding:16px;max-width:1150px;margin:auto}
h1{font:800 20px monospace;margin:4px 0 12px}h1 small{color:var(--m);font-weight:400;font-size:13px}
h2{font:700 11px monospace;text-transform:uppercase;letter-spacing:1px;color:var(--m);margin:0 0 10px}
.c{background:var(--c);border:1px solid var(--l);border-radius:14px;padding:14px;margin-bottom:14px;min-width:0}
.row{display:grid;gap:14px;grid-template-columns:1fr 1fr}@media(max-width:820px){.row{grid-template-columns:1fr}}
button{padding:9px 13px;border:1px solid var(--l);background:var(--c);color:var(--ink);border-radius:9px;font:600 13px system-ui;cursor:pointer}
button.on,button.go{background:var(--EM);color:#fff;border-color:var(--EM)}.seg{display:flex;gap:6px;flex-wrap:wrap}
input,select,textarea{width:100%;padding:9px;border:1px solid var(--l);border-radius:9px;background:var(--bg);color:var(--ink);font:14px system-ui}
.f{display:grid;grid-template-columns:1fr 1fr;gap:10px}.lab{font:600 12px system-ui;color:var(--m);margin:10px 0 4px;display:block}
.pill{display:inline-block;padding:2px 8px;border-radius:99px;font:700 11px monospace;color:#fff}.EMERGENCY{background:var(--EM)}.NORMAL{background:var(--NO)}.BULK{background:var(--BU)}
.peer{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid var(--l);font:13px monospace}
table{width:100%;border-collapse:collapse;font:13px monospace}th,td{padding:6px;border-bottom:1px solid var(--l);text-align:left}th{color:var(--m)}
svg{width:100%;height:auto;display:block}svg text{fill:var(--m);font:10px monospace}.g{stroke:var(--l)}
#msg{font:12px monospace;color:var(--m);min-height:16px;margin-top:6px}.feed{max-height:260px;overflow:auto;font:13px monospace}.feed div{padding:4px 0;border-bottom:1px solid var(--l)}
.leg{display:flex;gap:14px;font-size:12px;color:var(--m);flex-wrap:wrap}.q{display:flex;gap:10px;margin-top:8px}.q div{flex:1;border:1px solid var(--l);border-radius:10px;padding:8px;text-align:center;font:12px monospace}.q b{display:block;font-size:20px}
</style></head><body>
<h1>AlertApp <small id=me></small></h1>
<div class=row>
<div class=c><h2>1 · Queue policy</h2><div class=seg id=modes></div><div id=desc style="font-size:13px;color:var(--m);margin-top:8px"></div>
<span class=lab>Emulated uplink speed (kbit/s, 0 = unlimited). The queue only builds up when traffic exceeds this.</span><div class=seg><input id=link type=number style="width:130px"><button onclick="P('/api/link',{kbps:$('#link').value})">Set</button><button onclick="P('/api/reset',{})">Clear stats</button></div><div id=msg></div></div>
<div class=c><h2>2 · Peers (other machines)</h2><div id=peers></div>
<span class=lab>Join another machine running AlertApp</span><div class=f><input id=jip placeholder="IP address, e.g. 192.168.1.20"><input id=jport type=number value=5000></div>
<div class=seg style="margin-top:8px"><button class=go onclick="P('/api/join',{ip:$('#jip').value,port:$('#jport').value})">Join</button></div></div>
</div>
<div class=row>
<div class=c><h2>3 · Send a message</h2><textarea id=text rows=2 placeholder="Message text"></textarea>
<div class=f><div><span class=lab>Class</span><select id=cls><option>EMERGENCY</option><option>NORMAL</option></select></div><div><span class=lab>To</span><select id=to></select></div></div>
<div class=seg style="margin-top:10px"><button class=go onclick="P('/api/send',{text:$('#text').value,cls:$('#cls').value,to:$('#to').value})">Send</button></div></div>
<div class=c><h2>4 · Background traffic generator (BULK)</h2><div class=f><div><span class=lab>Packets per second (per peer)</span><input id=pps type=number value=200></div><div><span class=lab>Packet size (bytes)</span><input id=size type=number value=1000></div></div>
<div class=seg style="margin-top:10px"><button class=go onclick="G(true)">Start</button><button onclick="G(false)">Stop</button></div><div id=gl style="font:12px monospace;color:var(--m);margin-top:8px"></div></div>
</div>
<div class=c><h2>Traffic monitor · sent kbit/s per class, last 60 s</h2><svg id=ch viewBox="0 0 680 200"></svg>
<div class=leg><span><span class="pill EMERGENCY">EMERGENCY</span> port N, ACKed</span><span><span class="pill NORMAL">NORMAL</span> port N+2, ACKed</span><span><span class="pill BULK">BULK</span> port N+2, no ACK</span></div>
<div class=q id=q></div></div>
<div class=row><div class=c><h2>Delivery by class (RTT = enqueue to ACK, includes queue wait)</h2><div style="overflow-x:auto"><table id=t></table></div></div>
<div class=c><h2>Messages and events</h2><div class=feed id=feed></div></div></div>
<script>
const $=s=>document.querySelector(s),CL=['EMERGENCY','NORMAL','BULK'],COL={EMERGENCY:'var(--EM)',NORMAL:'var(--NO)',BULK:'var(--BU)'},MS=['static','priority','dynamic'],
D={static:'One FIFO queue: every class waits in the same line (no segregation).',priority:'Strict priority: EMERGENCY is always sent first.',dynamic:'Priority, and BULK is dropped when the queue builds up.'};
let peerKey='',first=true;
async function P(p,b){const r=await(await fetch(p,{method:'POST',body:JSON.stringify(b)})).json();$('#msg').textContent=r.result}
function G(on){P('/api/gen',{on,pps:$('#pps').value,size:$('#size').value})}
const f=v=>v==null?'–':v;
function nice(m){const e=Math.pow(10,Math.floor(Math.log10(m||1))),x=(m||1)/e;return(x<=1?1:x<=2?2:x<=5?5:10)*e}
async function tick(){const s=await(await fetch('/api/state')).json();
$('#me').textContent=s.name+' · listening on UDP '+s.port+' and '+(s.port+2);
$('#modes').innerHTML=MS.map(m=>`<button class="${s.mode==m?'on':''}" onclick="P('/api/mode',{mode:'${m}'})">${m}</button>`).join('');$('#desc').textContent=D[s.mode];
if(first){$('#link').value=s.link;first=false}
$('#gl').textContent=s.gen.on?`generator ON: ${s.gen.pps} pkt/s x ${s.gen.size} B per peer`:'generator off';
$('#peers').innerHTML=s.peers.length?s.peers.map(p=>`<div class=peer><span>${p.name} <small>${p.k}</small></span><button onclick="P('/api/leave',{key:'${p.k}'})">remove</button></div>`).join(''):'<small style="color:var(--m)">no peers yet</small>';
const keys=['ALL'].concat(s.peers.map(p=>p.k)).join(),sel=$('#to');if(sel.dataset.k!=keys){sel.dataset.k=keys;sel.innerHTML=keys.split(',').map(k=>`<option>${k}</option>`).join('')}
$('#q').innerHTML=CL.map(c=>`<div><span class="pill ${c}">${c}</span><b>${s.queue[c]}</b>waiting in queue</div>`).join('');
$('#t').innerHTML='<tr><th>class<th>sent<th>acked<th>retries<th>failed<th>dropped<th>received<th>avg RTT ms<th>p95 ms</tr>'+CL.map(c=>{const x=s.stats[c];return `<tr><td><span class="pill ${c}">${c}</span><td>${x.sent}<td>${x.acked}<td>${x.retries}<td>${x.failed}<td>${x.dropped}<td>${x.rx}<td>${f(x.avg)}<td>${f(x.p95)}</tr>`}).join('');
$('#feed').innerHTML=s.feed.slice().reverse().map(e=>`<div><small>${e.t}</small> <span class="pill ${CL.includes(e.kind)?e.kind:'BULK'}">${e.kind}</span> ${e.text.replace(/</g,'&lt;')}</div>`).join('')||'<small style="color:var(--m)">nothing yet</small>';
const W=680,H=200,L=44,T=8,B=20,pw=W-L-8,ph=H-T-B;let mx=1;s.series.forEach(p=>CL.forEach(c=>mx=Math.max(mx,p[c][0])));const top=nice(mx*1.1),Y=v=>T+ph-v/top*ph,X=i=>L+i/59*pw;let o='';
for(let i=0;i<=4;i++){const v=top*i/4;o+=`<line class=g x1=${L} x2=${W-8} y1=${Y(v)} y2=${Y(v)}></line><text x=${L-6} y=${Y(v)+3} text-anchor=end>${f(Math.round(v))}</text>`}
CL.forEach(c=>{o+=`<polyline fill=none stroke-width=2 stroke="${COL[c]}" points="${s.series.map((p,i)=>X(i)+','+Y(p[c][0])).join(' ')}"></polyline>`});
o+=`<text x=${L} y=${H-4}>60 s ago</text><text x=${W-8} y=${H-4} text-anchor=end>now</text>`;$('#ch').innerHTML=o}
tick();setInterval(tick,1000);
</script></body></html>"""


def make_handler(node):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, body, ctype='application/json'):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/api/state':
                self.reply(json.dumps(node.state()))
            elif self.path in ('/', '/index.html'):
                self.reply(PAGE, 'text/html; charset=utf-8')
            else:
                self.send_error(404)

        def do_POST(self):
            n = int(self.headers.get('Content-Length', 0) or 0)
            try:
                body = json.loads(self.rfile.read(n) or b'{}')
                result = node.act(self.path, body)
            except (ValueError, TypeError) as e:
                result = f'bad request: {e}'
            self.reply(json.dumps({'result': result}))
    return H


def main():
    ap = argparse.ArgumentParser(description='UDP alert messaging between machines, with a web UI')
    ap.add_argument('--name', default=socket.gethostname())
    ap.add_argument('--port', type=int, default=5000, help='UDP port for EMERGENCY (NORMAL/BULK use port+2)')
    ap.add_argument('--ui-port', type=int, default=8080)
    ap.add_argument('--ui-host', default='127.0.0.1', help='use 0.0.0.0 to open the UI to other machines')
    ap.add_argument('--ctl-ip', default='', help='optional: send alert_start/alert_end events to an SDN controller IP (port 9998)')
    a = ap.parse_args()
    node = Node(a)
    print(f'AlertApp "{a.name}": UDP {a.port} (emergency) and {a.port + 2} (normal/bulk)')
    print(f'UI: http://localhost:{a.ui_port}   (allow UDP {a.port} and {a.port + 2} in your firewall)')
    try:
        ThreadingHTTPServer((a.ui_host, a.ui_port), make_handler(node)).serve_forever()
    except KeyboardInterrupt:
        for p in list(node.peers.values()):
            try:
                node.socks[node.port].sendto(json.dumps({'t': 'BYE', 'port': node.port}).encode(), (p['ip'], p['port']))
            except OSError:
                pass


if __name__ == '__main__':
    main()
