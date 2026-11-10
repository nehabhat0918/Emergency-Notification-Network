"""Emergency alert app with a web UI. Same UDP protocol as server.py / client.py.

  Sender computer:    python3 alert_ui.py server
                      then open http://localhost:8080 and use the form
  Receiver computer:  python3 alert_ui.py client <server_ip>
                      then open http://localhost:8081 to see alerts arrive
"""
import socket, threading, time, csv, os, sys, json, argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 5000
TIMEOUT = 0.5
RETRIES = 3
CTL = ("10.0.0.100", 9998)   # virtual controller IP (SDN event channel, same as server.py)
TYPES = ["FIRE", "MEDICAL", "SECURITY", "GENERAL"]

# ---------------------------------------------------------------- web helper
def run_web(host, port, page, get_api, post_api):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def _send(self, code, body, ctype):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            if self.path == "/":
                self._send(200, page, "text/html; charset=utf-8")
            elif self.path.startswith("/api/"):
                self._send(200, json.dumps(get_api(self.path)), "application/json")
            else:
                self._send(404, "not found", "text/plain")
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            code, resp = post_api(self.path, body)
            self._send(code, json.dumps(resp), "application/json")
    srv = ThreadingHTTPServer((host, port), H)
    print(f"UI running at http://{'localhost' if host in ('127.0.0.1','0.0.0.0') else host}:{port}")
    srv.serve_forever()

# ---------------------------------------------------------------- SERVER MODE
SERVER_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Emergency Alert - Sender</title>
<style>
body{font-family:system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;background:#f4f5f7;color:#1c1c1c}
h1{margin-bottom:4px} .sub{color:#666;margin-bottom:20px}
.card{background:#fff;border-radius:10px;padding:16px;margin-bottom:16px;box-shadow:0 1px 3px #0002}
label{font-size:13px;color:#555;display:block;margin-bottom:4px}
.row{display:flex;gap:12px;flex-wrap:wrap}
select,input{padding:10px;font-size:15px;border:1px solid #bbb;border-radius:6px}
input.msg{flex:1;min-width:240px}
button{padding:12px 26px;font-size:16px;font-weight:600;color:#fff;background:#c62828;border:0;border-radius:6px;cursor:pointer}
button:hover{background:#a71d1d}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid #eee}
.ok{color:#1b7f3b;font-weight:600}.bad{color:#c62828;font-weight:600}.wait{color:#b26a00}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;background:#eee;font-size:12px;margin:2px}
</style></head><body>
<h1>Emergency Alert - Sender</h1>
<div class="sub">UDP port <span id="port"></span> &middot; <span id="count">0</span> client(s) connected</div>

<div class="card">
  <div class="row">
    <div><label>Type</label><select id="type"></select></div>
    <div><label>Priority</label><select id="prio">
      <option value="1">1 - High</option><option value="2">2 - Medium</option><option value="3">3 - Low</option></select></div>
    <div style="flex:1"><label>Message</label><input id="msg" class="msg" style="width:100%;box-sizing:border-box" placeholder="e.g. Fire in the main building"></div>
  </div>
  <div style="margin-top:14px"><button onclick="send()">SEND ALERT</button> <span id="err" class="bad"></span></div>
</div>

<div class="card"><b>Connected clients</b><div id="clients" style="margin-top:8px"></div></div>

<div class="card"><b>Sent alerts</b>
<table><thead><tr><th>#</th><th>Time</th><th>Type</th><th>P</th><th>Message</th><th>Delivery</th></tr></thead>
<tbody id="hist"></tbody></table></div>

<script>
const TYPES = %TYPES%;
document.getElementById('type').innerHTML = TYPES.map(t=>'<option>'+t+'</option>').join('');
document.getElementById('port').textContent = %PORT%;
document.getElementById('msg').addEventListener('keydown',e=>{if(e.key==='Enter')send()});
async function send(){
  const m=document.getElementById('msg').value.trim(), err=document.getElementById('err');
  if(!m){err.textContent='Type a message first';return}
  err.textContent='';
  const r=await fetch('/api/send',{method:'POST',body:JSON.stringify({
    type:document.getElementById('type').value,priority:document.getElementById('prio').value,message:m})});
  const j=await r.json();
  if(!r.ok){err.textContent=j.error||'failed';return}
  document.getElementById('msg').value='';
  refresh();
}
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function refresh(){
  const s=await (await fetch('/api/state')).json();
  document.getElementById('count').textContent=s.clients.length;
  document.getElementById('clients').innerHTML=s.clients.length?
    s.clients.map(c=>'<span class="pill">'+c+'</span>').join(''):'<i>none yet - start the client on the other computer</i>';
  document.getElementById('hist').innerHTML=s.history.map(h=>{
    const d=Object.entries(h.clients).map(([c,st])=>{
      const cls=st==='ACKED'?'ok':(st==='UNREACHABLE'?'bad':'wait');
      return '<span class="'+cls+'">'+c+': '+st+'</span>';}).join('<br>')||'<span class="wait">'+h.status+'</span>';
    return '<tr><td>'+h.seq+'</td><td>'+h.time+'</td><td>'+h.type+'</td><td>'+h.priority+'</td><td>'+esc(h.message)+'</td><td>'+d+'</td></tr>';
  }).join('');
}
setInterval(refresh,1000);refresh();
</script></body></html>"""

def server_mode(a):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", PORT))
    LOG = "server_log.csv"
    if not os.path.exists(LOG):
        with open(LOG, "w", newline="") as f:
            csv.writer(f).writerow(["seq", "type", "priority", "client", "send_time",
                                    "ack_time", "rtt_ms", "attempts", "status"])
    lock = threading.Lock()
    clients = set()
    acked = {}
    history = []
    state = {"seq": 0}

    def notify(kind, **extra):
        """Tell the SDN controller what the app is doing (ignored if no controller)."""
        try:
            sock.sendto(json.dumps({"type": kind, **extra}).encode(), CTL)
        except OSError:
            pass

    def receiver():
        while True:
            data, addr = sock.recvfrom(1024)
            text = data.decode(errors="ignore")
            if text == "REGISTER":
                with lock:
                    clients.add(addr)
                notify("register", client=addr[0])
                print(f"registered {addr}")
            elif text == "LEAVE":
                with lock:
                    clients.discard(addr)
                print(f"left {addr}")
            elif text.startswith("BULK|"):
                try:
                    _, n, gap = text.split("|")
                    threading.Thread(target=bulk, args=(int(n), float(gap)), daemon=True).start()
                except ValueError:
                    pass
            elif text.startswith("ACK|"):
                try:
                    s = int(text.split("|")[1])
                except ValueError:
                    continue
                with lock:
                    if s in acked and addr not in acked[s]:
                        acked[s][addr] = time.time()

    def deliver(atype, prio, msg):
        """One alert from the web form: tells the controller an alert starts and ends."""
        notify("alert_start")
        try:
            _deliver(atype, prio, msg)
        finally:
            notify("alert_end")

    def _deliver(atype, prio, msg):
        with lock:
            state["seq"] += 1
            seq = state["seq"]
            targets = set(clients)
            acked[seq] = {}
            entry = {"seq": seq, "time": time.strftime("%H:%M:%S"), "type": atype,
                     "priority": prio, "message": msg, "status": "sending",
                     "clients": {f"{c[0]}:{c[1]}": "sending..." for c in targets}}
            history.append(entry)
        if not targets:
            entry["status"] = "no clients connected"
            return
        t_send = time.time()
        packet = f"{seq}|{t_send}|{atype}|{prio}|{msg}".encode()
        pending = set(targets)
        attempts = {}
        for attempt in range(RETRIES + 1):
            for addr in pending:
                sock.sendto(packet, addr)
            deadline = time.time() + TIMEOUT
            while time.time() < deadline:
                with lock:
                    still = targets - set(acked[seq])
                if not still:
                    break
                time.sleep(0.01)
            with lock:
                still = targets - set(acked[seq])
            for addr in pending - still:
                attempts[addr] = attempt + 1
            pending = still
            if not pending:
                break
        with open(LOG, "a", newline="") as f:
            w = csv.writer(f)
            for addr in targets:
                key = f"{addr[0]}:{addr[1]}"
                if addr in acked[seq]:
                    t_ack = acked[seq][addr]
                    w.writerow([seq, atype, prio, key, f"{t_send:.6f}", f"{t_ack:.6f}",
                                f"{(t_ack - t_send) * 1000:.3f}", attempts[addr], "ACKED"])
                    entry["clients"][key] = "ACKED"
                else:
                    w.writerow([seq, atype, prio, key, f"{t_send:.6f}", "", "", RETRIES + 1, "UNREACHABLE"])
                    entry["clients"][key] = "UNREACHABLE"
        entry["status"] = "done"
        print(f"alert #{seq} {atype} P{prio}: " +
              ("all ACKed" if not pending else f"{len(pending)} unreachable"))

    def bulk(n, gap):
        notify("alert_start")
        for i in range(n):
            _deliver("FIRE", "1", f"test alert {i+1}")
            time.sleep(gap)
        notify("alert_end")
        print(f"bulk done: {n} alerts")

    def get_api(path):
        with lock:
            return {"clients": sorted(f"{c[0]}:{c[1]}" for c in clients),
                    "history": list(reversed(history[-30:]))}

    def post_api(path, body):
        if path != "/api/send":
            return 404, {"error": "not found"}
        atype = str(body.get("type", "")).upper()
        prio = str(body.get("priority", ""))
        msg = str(body.get("message", "")).strip().replace("|", "/")
        if atype not in TYPES or prio not in ("1", "2", "3") or not msg:
            return 400, {"error": "need type, priority 1-3 and a message"}
        threading.Thread(target=deliver, args=(atype, prio, msg), daemon=True).start()
        return 200, {"ok": True}

    threading.Thread(target=receiver, daemon=True).start()
    print(f"Alert server listening on UDP port {PORT}")
    page = SERVER_PAGE.replace("%TYPES%", json.dumps(TYPES)).replace("%PORT%", str(PORT))
    run_web(a.ui_host, a.ui_port or 8080, page, get_api, post_api)

# ---------------------------------------------------------------- CLIENT MODE
CLIENT_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Emergency Alert - Receiver</title>
<style>
body{font-family:system-ui,sans-serif;max-width:800px;margin:24px auto;padding:0 16px;background:#f4f5f7;color:#1c1c1c}
h1{margin-bottom:4px}.sub{color:#666;margin-bottom:16px}
#banner{border-radius:10px;padding:22px;margin-bottom:16px;background:#e8f5e9;color:#1b5e20;font-size:20px}
#banner.alert{background:#c62828;color:#fff;animation:flash 0.6s 4}
@keyframes flash{50%{background:#ff8a80;color:#600}}
.card{background:#fff;border-radius:10px;padding:16px;box-shadow:0 1px 3px #0002}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid #eee}
</style></head><body>
<h1>Emergency Alert - Receiver</h1>
<div class="sub">Connected to <b id="srv"></b> &middot; <span id="n">0</span> alert(s) received</div>
<div id="banner">Waiting for alerts...</div>
<div class="card"><table><thead><tr><th>#</th><th>Time</th><th>Type</th><th>P</th><th>Message</th><th>Latency</th></tr></thead>
<tbody id="rows"></tbody></table></div>
<script>
document.getElementById('srv').textContent='%SERVER%';
let last=0, ctx=null;
function beep(){try{ctx=ctx||new (window.AudioContext||window.webkitAudioContext)();
 const o=ctx.createOscillator(),g=ctx.createGain();o.connect(g);g.connect(ctx.destination);
 o.frequency.value=880;g.gain.value=0.2;o.start();setTimeout(()=>o.stop(),400);}catch(e){}}
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function refresh(){
  const a=await (await fetch('/api/alerts')).json();
  document.getElementById('n').textContent=a.length;
  if(a.length>last){
    const x=a[0], b=document.getElementById('banner');
    b.className='alert';b.innerHTML='<b>'+x.type+' (P'+x.priority+')</b><br>'+esc(x.message);
    beep();setTimeout(()=>{b.className=''},4000);
  }
  last=a.length;
  document.getElementById('rows').innerHTML=a.map(x=>'<tr><td>'+x.seq+'</td><td>'+x.time+'</td><td>'+x.type+
    '</td><td>'+x.priority+'</td><td>'+esc(x.message)+'</td><td>'+x.latency+' ms</td></tr>').join('');
}
setInterval(refresh,700);refresh();
</script></body></html>"""

def client_mode(a):
    server_ip = a.server_ip
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"REGISTER", (server_ip, PORT))
    logname = f"client_log_{os.getpid()}.csv"
    logf = open(logname, "w", newline="")
    log = csv.writer(logf)
    log.writerow(["seq", "type", "priority", "send_ts", "recv_ts", "latency_ms"])
    logf.flush()
    alerts = []   # newest first
    seen = set()

    def receiver():
        while True:
            data, addr = sock.recvfrom(1024)
            now = time.time()
            try:
                seq, ts, atype, prio, msg = data.decode().split("|", 4)
                latency = (now - float(ts)) * 1000
            except ValueError:
                continue
            sock.sendto(f"ACK|{seq}".encode(), addr)
            if seq in seen:
                continue
            seen.add(seq)
            log.writerow([seq, atype, prio, ts, f"{now:.6f}", f"{latency:.3f}"])
            logf.flush()
            alerts.insert(0, {"seq": seq, "type": atype, "priority": prio, "message": msg,
                              "time": time.strftime("%H:%M:%S"), "latency": f"{latency:.2f}"})
            print(f"[{atype} P{prio}] #{seq}: {msg} ({latency:.2f} ms)")

    threading.Thread(target=receiver, daemon=True).start()
    print(f"Registered with {server_ip}:{PORT}, logging to {logname}")
    page = CLIENT_PAGE.replace("%SERVER%", f"{server_ip}:{PORT}")
    try:
        run_web(a.ui_host, a.ui_port or 8081, page,
                lambda p: alerts[:100], lambda p, b: (404, {}))
    except KeyboardInterrupt:
        sock.sendto(b"LEAVE", (server_ip, PORT))
        logf.close()
        print("\nLeft the network")

# ---------------------------------------------------------------- main
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("server")
    c = sub.add_parser("client")
    c.add_argument("server_ip")
    for x in (s, c):
        x.add_argument("--ui-port", type=int, default=0)
        x.add_argument("--ui-host", default="127.0.0.1")
    a = p.parse_args()
    try:
        server_mode(a) if a.mode == "server" else client_mode(a)
    except KeyboardInterrupt:
        print("\nstopped")
