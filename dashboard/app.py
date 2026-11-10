#!/usr/bin/env python3
"""
Live dashboard for the SDN emergency-alert project (uses YOUR code + logs).

Run from the project root, while the Mininet topology is up:
    sudo python3 dashboard/app.py            # then open http://<vm-ip>:8000

It (1) reads server_log.csv + client_log_*.csv live,
   (2) controls the network by running your own scripts inside Mininet hosts
       via `mnexec` (ctl_send.py, bulk.py, iperf) and `ip link` for failures.
Stdlib only - no pip installs.
"""
import csv, glob, json, os, re, socket, statistics, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.getcwd()
CTL_SEND = os.path.join(ROOT, "src", "ctl_send.py")      # edit if yours is elsewhere
SENDALERT = os.path.join(ROOT, "sendalert.py")              # single alert sender (talks to server.py)
TYPES = ("FIRE", "MEDICAL", "SECURITY", "GENERAL")
BULK = os.path.join(ROOT, "bulk.py")                      # the BULK sender script
BOTTLENECK_IF = ("s1-eth3", "s3-eth5")                    # direct link ports (network_config.LINKS)
state = {"mode": os.environ.get("SDN_MODE", "dynamic"), "iperf": False, "link": "up", "msg": ""}
state["switches"] = [[0, state["mode"]]]   # (timestamp, mode) history, used to label each alert with its mode


def host_pid(h):
    r = subprocess.run(["pgrep", "-f", f"mininet:{h}\\b"], capture_output=True, text=True)
    return r.stdout.split()[0] if r.stdout.split() else None


def on_host(h, cmd, wait=False):
    pid = host_pid(h)
    if not pid:
        return f"host {h} not found - is Mininet running?"
    full = ["mnexec", "-a", pid] + cmd
    if wait:
        r = subprocess.run(full, capture_output=True, text=True, cwd=ROOT)
        return (r.stdout + r.stderr).strip()
    subprocess.Popen(full, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "started"


def num(x, lo, hi, default):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def act(a, v=None):
    if a == "mode" and v in ("static", "priority", "dynamic"):
        state["mode"] = v
        state["switches"].append([time.time(), v])
        return on_host("h1", ["python3", CTL_SEND, "mode", v], True) or "sent"
    if a == "bulk":
        v = v if isinstance(v, dict) else {"n": v}
        n = int(num(v.get("n"), 1, 5000, 100))
        gap = num(v.get("gap"), 0.001, 5, 0.05)
        return on_host("h1", ["python3", BULK, str(n), f"{gap:g}"]) + f" ({n} alerts, {gap * 1000:g} ms apart)"
    if a == "send":
        v = v if isinstance(v, dict) else {}
        t, p, m = str(v.get("type", "")).upper(), str(v.get("prio", "")), str(v.get("msg", "")).strip()[:200]
        if t not in TYPES or p not in ("1", "2", "3") or not m:
            return "need a type, a priority (1-3) and a message"
        r = on_host("h1", ["python3", SENDALERT, t, p, m])
        return f"{t} P{p} sent to the server" if r == "started" else r
    if a == "iperf_on":
        v = v if isinstance(v, dict) else {}
        port = 5000 if v.get("cls") == "emergency" else 5001   # port 5000 = emergency class, 5001 = normal
        rate = num(v.get("rate"), 0.1, 1000, 20)
        dur = int(num(v.get("dur"), 1, 3600, 120))
        subprocess.run("pkill -f 'iperf'", shell=True)
        time.sleep(0.3)
        on_host("h6", ["iperf", "-s", "-u", "-p", str(port)])
        time.sleep(0.5)
        state["iperf"] = True
        r = on_host("h5", ["iperf", "-u", "-c", "10.0.0.6", "-p", str(port), "-b", f"{rate:g}M", "-t", str(dur)])
        return f"{r}: {rate:g} Mbps for {dur} s as {'emergency' if port == 5000 else 'normal'} traffic (UDP {port})"
    if a == "iperf_off":
        subprocess.run("pkill -f 'iperf'", shell=True)
        state["iperf"] = False
        return "stopped"
    if a in ("link_down", "link_up"):
        up = "up" if a == "link_up" else "down"
        for i in BOTTLENECK_IF:
            subprocess.run(["ip", "link", "set", i, up])
        state["link"] = up
        return f"link s1-s3 {up}"
    if a == "clear":
        for f in ["server_log.csv"] + glob.glob("client_log_*.csv"):
            try:
                os.remove(f)
            except OSError:
                pass
        return "logs removed (restart server.py so it rewrites the header)"
    return "unknown action"


NET = {"ok": False}   # live switch counters, refreshed once a second by monitor()
HIST = []             # last 60 samples of [direct Mbps, alternate Mbps]


def ovs(*a):
    try:
        return subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", *a], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_flows(txt):
    """Packets and bytes per flow priority: 100 = emergency class, 10 = normal class."""
    out = {}
    for pk, by, pr in re.findall(r"n_packets=(\d+), n_bytes=(\d+), priority=(\d+)", txt):
        c = out.setdefault(int(pr), [0, 0])
        c[0] += int(pk)
        c[1] += int(by)
    return out


def parse_ports(txt):
    """port number -> (tx_bytes, tx_dropped) from `ovs-ofctl dump-ports`."""
    out = {}
    for name, by, dr in re.findall(r"port\s+(\S+): rx pkts=[^\n]*\n\s+tx pkts=\d+, bytes=(\d+), drop=(\d+)", txt):
        m = re.search(r"(\d+)$", name)
        if m:
            out[int(m.group(1))] = (int(by), int(dr))
    return out


def monitor():
    prev = None
    while True:
        time.sleep(1)
        fl, po = parse_flows(ovs("dump-flows", "s1")), parse_ports(ovs("dump-ports", "s1"))
        if not fl or 3 not in po:
            NET["ok"] = False
            prev = None
            continue
        now = time.time()
        if prev:
            dt = max(now - prev[0], 0.05)
            d = lambda a, b: max(0, a - b)
            direct = d(po[3][0], prev[2][3][0]) * 8 / dt / 1e6
            alt = d(po.get(4, (0, 0))[0], prev[2].get(4, (0, 0))[0]) * 8 / dt / 1e6
            NET.update(ok=True, direct=direct, alt=alt, drops=d(po[3][1], prev[2][3][1]) / dt,
                       em_pps=d(fl.get(100, [0])[0], prev[1].get(100, [0])[0]) / dt,
                       be_pps=d(fl.get(10, [0])[0], prev[1].get(10, [0])[0]) / dt)
            HIST.append([round(direct, 2), round(alt, 2)])
            del HIST[:-60]
        prev = (now, fl, po)


def read_jsonl(path, n):
    try:
        with open(path) as f:
            lines = f.readlines()[-n:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out[::-1]


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "<this-machine-ip>"


def read_csv(path):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def mode_at(t):
    m = state["switches"][0][1]
    for ts, md in state["switches"]:
        if ts <= t:
            m = md
    return m


def per_mode(rows):
    """Group every delivery by the controller mode that was active when it was sent."""
    clat = {"static": [], "priority": [], "dynamic": []}
    for f in glob.glob("client_log_*.csv"):
        for r in read_csv(f):
            try:
                clat[mode_at(float(r["send_ts"]))].append(float(r["latency_ms"]))
            except (KeyError, ValueError):
                pass
    out = {}
    for m in ("static", "priority", "dynamic"):
        sel = []
        for r in rows:
            try:
                if mode_at(float(r["send_time"])) == m:
                    sel.append(r)
            except (KeyError, ValueError):
                pass
        n = len(sel)
        ack = [r for r in sel if r.get("status") == "ACKED"]
        retr = [r for r in ack if int(r["attempts"]) > 1]
        lat = sorted(clat[m])
        d = {"sent": n, "acked": len(ack), "retried": len(retr), "unreachable": n - len(ack),
             "loss": round((n - len(ack)) / n * 100, 2) if n else None,
             "first_try_loss": round(len(retr) / n * 100, 2) if n else None}
        if lat:
            d.update(avg=round(statistics.mean(lat), 2), p95=round(lat[max(0, int(len(lat) * .95) - 1)], 2), max=round(lat[-1], 2))
        out[m] = d
    return out


def stats():
    rows = read_csv("server_log.csv")
    sent = len(rows)
    ack = [r for r in rows if r.get("status") == "ACKED"]
    retried = [r for r in ack if int(r["attempts"]) > 1]
    rtt = [float(r["rtt_ms"]) for r in ack if r["rtt_ms"]]
    lat = []
    for f in glob.glob("client_log_*.csv"):
        lat += [float(r["latency_ms"]) for r in read_csv(f)]
    lat.sort()
    pct = lambda n: round(n / sent * 100, 2) if sent else 0
    out = {"sent": sent, "acked": len(ack), "retried": len(retried), "unreachable": sent - len(ack),
           "loss": pct(sent - len(ack)), "first_try_loss": pct(len(retried)),
           "rtt_recent": rtt[-80:], "state": state}
    if lat:
        out["lat"] = {"avg": round(statistics.mean(lat), 2), "median": round(statistics.median(lat), 2),
                      "p95": round(lat[max(0, int(len(lat) * .95) - 1)], 2), "max": round(lat[-1], 2)}
    if rtt:
        out["rtt_avg"] = round(statistics.mean(rtt), 2)
    out["by_mode"] = per_mode(rows)
    out["net"], out["hist"] = NET, HIST
    out["feed"] = read_jsonl("alert_feed.jsonl", 12)
    try:
        out["clients"] = json.load(open("clients.json"))
    except (OSError, ValueError):
        out["clients"] = []
    out["recent"] = [{k: r[k] for k in ("seq", "type", "client", "rtt_ms", "attempts", "status")} for r in rows[-12:]][::-1]
    return out


PAGE = r"""<!DOCTYPE html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>SDN Alert Console</title><style>
:root{--bg:#f4f1ea;--c:#fffdf8;--ink:#1d1b16;--m:#6f6a5d;--l:#ddd6c6;--em:#d9480f;--bad:#c92a2a;--st:#d64545;--pr:#3b6fb6;--dy:#2f9e5a;--p95:#e8892b;--mx:#8b5cf6}
@media(prefers-color-scheme:dark){:root{--bg:#12110d;--c:#1d1b15;--ink:#f0ece0;--m:#9a947f;--l:#36331f;--em:#ff8a4c;--bad:#ff6b6b;--st:#ff6b6b;--pr:#6fa3ec;--dy:#5ccf84;--p95:#ffb35c;--mx:#b08cf0}}
html{overflow-anchor:none;scroll-behavior:smooth}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px system-ui,sans-serif;padding:16px;max-width:1240px;margin:auto}
h1{font:800 20px monospace;margin:4px 0 12px}h1 b{color:var(--em)}h1 small{color:var(--m);font-weight:400}
h2{font:700 11px monospace;text-transform:uppercase;letter-spacing:1px;color:var(--m);margin:0 0 10px}
.c{background:var(--c);border:1px solid var(--l);border-radius:14px;padding:14px;margin-bottom:14px;min-width:0;contain:layout paint style}
body.scrolling *{animation-play-state:paused!important}body.scrolling{pointer-events:none}
.row{display:grid;gap:14px}.r2{grid-template-columns:1fr 1fr}.r3{grid-template-columns:repeat(3,1fr)}.r21{grid-template-columns:1.6fr 1fr}
@media(max-width:860px){.r2,.r3,.r21{grid-template-columns:1fr}}
button{padding:9px 13px;border:1px solid var(--l);background:var(--c);color:var(--ink);border-radius:9px;font:600 13px system-ui;cursor:pointer}
button:hover{border-color:var(--em)}button.on{background:var(--em);color:#fff;border-color:var(--em)}button.go{background:var(--em);color:#fff;border-color:var(--em)}button.dg{color:var(--bad)}button.rd{background:var(--bad);color:#fff;border-color:var(--bad)}button.gn{background:var(--dy);color:#fff;border-color:var(--dy)}
.tp{margin-top:12px}.tp .pr{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}.pill{display:inline-block;padding:6px 12px;border-radius:99px;font:700 12px monospace;color:#fff;background:var(--m)}.pill.g{background:var(--dy)}.pill.o{background:var(--p95)}.pill.r{background:var(--bad)}
.tp .nb{fill:var(--c);stroke:var(--l);stroke-width:1.5}.tp .nb.hot{stroke:var(--p95);stroke-width:3}.tp .hl{stroke:var(--m);stroke-width:2;fill:none;opacity:.6}
.tp #dl{stroke-width:6;stroke-linecap:round;fill:none}.tp #dl.up{stroke:var(--dy)}.tp #dl.down{stroke:var(--bad);stroke-dasharray:4 9}.tp #dl.flow{stroke-dasharray:10 7;animation:fl .6s linear infinite}
.tp #al{stroke:var(--p95);stroke-width:4;fill:none;stroke-dasharray:8 7;opacity:.3}.tp #al.on{opacity:1}.tp #al.flow{animation:fl .6s linear infinite}
@keyframes fl{to{stroke-dashoffset:-34}}.tp #xm{display:none}.tp #xm.on{display:block}.tp #xm line{stroke:var(--bad);stroke-width:6;stroke-linecap:round}.tp #xm text{fill:var(--bad);font:700 13px monospace}

.seg{display:flex;gap:6px;flex-wrap:wrap}.lab{font:600 12px system-ui;color:var(--m);margin:10px 0 4px;display:block}
input[type=number]{width:100%;padding:9px;border:1px solid var(--l);border-radius:9px;background:var(--bg);color:var(--ink);font:15px monospace}
.f{display:grid;grid-template-columns:1fr 1fr;gap:10px}.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}.chips button{padding:5px 10px;font-size:12px}
.load{height:10px;border-radius:6px;background:var(--l);overflow:hidden;margin-top:8px;position:relative}.load i{display:block;height:100%;background:var(--dy);transition:width .2s}
.kp{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}.k{border:1px solid var(--l);border-radius:11px;padding:10px}.k b{display:block;font:700 22px monospace}.k small{color:var(--m);font-size:11px}
.mc{border-radius:14px;border:1px solid var(--l);border-top:5px solid var(--m);padding:14px;background:var(--c);position:relative}
.mc h3{margin:0;font:700 13px monospace;text-transform:uppercase}.mc .big{font:800 34px monospace;margin:6px 0 0}.mc .big small{font-size:14px;color:var(--m)}
.mc .sub{font-size:12px;color:var(--m);line-height:1.6}.badge{position:absolute;top:12px;right:12px;font:700 11px monospace;padding:3px 8px;border-radius:99px;background:var(--dy);color:#fff}
.mc.st{border-top-color:var(--st)}.mc.pr{border-top-color:var(--pr)}.mc.dy{border-top-color:var(--dy)}
svg{width:100%;height:auto;display:block}svg text{fill:var(--ink);font:11px monospace}svg .g{stroke:var(--l);stroke-width:1}svg .ax{fill:var(--m)}
.leg{display:flex;gap:14px;font-size:12px;color:var(--m);margin:6px 0 0;flex-wrap:wrap}.leg i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;vertical-align:-1px}
table{width:100%;border-collapse:collapse;font:13px monospace}th,td{padding:7px 6px;border-bottom:1px solid var(--l);text-align:left}th{color:var(--m);font-weight:600}.bad{color:var(--bad)}
#msg{font:12px monospace;color:var(--m);min-height:18px;margin-top:8px}.tabs{display:flex;gap:6px;margin-bottom:6px;flex-wrap:wrap}
select,input[type=text]{width:100%;padding:9px;border:1px solid var(--l);border-radius:9px;background:var(--bg);color:var(--ink);font:15px system-ui}
.fd{display:flex;gap:8px;align-items:baseline;padding:6px 0;border-bottom:1px solid var(--l);font-size:13px}.fd b{font:700 11px monospace;padding:2px 6px;border-radius:6px;color:#fff;background:var(--m)}.fd .p1{background:var(--bad)}.fd .p2{background:var(--p95)}.fd small{color:var(--m);margin-left:auto;font:11px monospace}
</style></head><body>
<h1>SDN<b>/</b>ALERT CONSOLE <small>live · reads your real logs</small></h1>
<div class="row r2">
 <div class=c><h2>1 · Controller mode</h2><div class=seg id=modes></div>
  <span class=lab>Link and logs</span><div class=seg><button id=bdown class=dg onclick="A('link_down')">link s1-s3 down</button><button id=bup class=gn onclick="A('link_up')">link up</button><button class=dg onclick="confirm('Delete logs? Stop server.py first.')&&A('clear')">clear logs</button></div>
  <div id=msg></div>
  <div class=tp>
   <div class=pr><span id=pa class=pill>alerts</span><span id=pb class=pill>background traffic</span></div>
   <svg id=tpsvg viewBox="0 0 600 330">
    <path class=hl d="M110 42L170 65M110 106L170 95M460 65L500 42M460 95L500 106"/>
    <path id=al d="M220 110V258H265M365 258H410V110"/>
    <path id=dl class=up d="M270 80H360"/>
    <g id=xm><line x1="305" y1="68" x2="325" y2="92"/><line x1="325" y1="68" x2="305" y2="92"/><text x="315" y="114" text-anchor="middle">LINK DOWN</text></g>
    <rect class=nb x=10 y=20 width=100 height=44 rx=8 /><text x=60 y=38 text-anchor=middle style="font-weight:700;font-size:13px">h1</text><text x=60 y=54 text-anchor=middle class=ax>server</text>
    <rect class=nb x=10 y=84 width=100 height=44 rx=8 /><text x=60 y=102 text-anchor=middle style="font-weight:700;font-size:13px">h5</text><text x=60 y=118 text-anchor=middle class=ax>iperf source</text>
    <rect class=nb x=170 y=50 width=100 height=60 rx=8 /><text x=220 y=76 text-anchor=middle style="font-weight:700;font-size:14px">s1</text><text x=220 y=94 text-anchor=middle class=ax>server side</text>
    <rect class=nb x=360 y=50 width=100 height=60 rx=8 /><text x=410 y=76 text-anchor=middle style="font-weight:700;font-size:14px">s3</text><text x=410 y=94 text-anchor=middle class=ax>client side</text>
    <rect class=nb x=500 y=20 width=90 height=44 rx=8 /><text x=545 y=38 text-anchor=middle style="font-weight:700;font-size:13px">h2, h3, h4</text><text x=545 y=54 text-anchor=middle class=ax>clients</text>
    <rect class=nb x=500 y=84 width=90 height=44 rx=8 /><text x=545 y=102 text-anchor=middle style="font-weight:700;font-size:13px">h6</text><text x=545 y=118 text-anchor=middle class=ax>iperf sink</text>
    <rect id=ns2 class=nb x=265 y=230 width=100 height=56 rx=8 /><text x=315 y=254 text-anchor=middle style="font-weight:700;font-size:14px">s2</text><text x=315 y=272 text-anchor=middle class=ax>alternate</text>
    <text x=315 y=40 text-anchor=middle class=ax>Direct link s1-s3</text>
    <text x=315 y=306 text-anchor=middle class=ax>Alternate path via s2</text>
   </svg>
   <div class=leg><span><i style="background:var(--dy)"></i>direct path up</span><span><i style="background:var(--bad)"></i>direct link DOWN</span><span><i style="background:var(--p95)"></i>detour via s2 (in use when bright)</span></div>
  </div></div>
 <div class=c><h2>2 · Traffic generator (any amount)</h2>
  <div class=f><div><span class=lab>Background traffic (Mbps)</span><input type=number id=rate value=20 min=0.1 max=1000 step=any></div><div><span class=lab>Duration (s)</span><input type=number id=dur value=120 min=1 max=3600></div></div>
  <span class=lab>Traffic class to send</span><select id=cls><option value=normal>Normal · UDP 5001 (best-effort queue)<option value=emergency>Emergency · UDP 5000 (priority queue)</select>
  <div class=chips id=chips></div>
  <div class=load><i id=ld></i></div><div style="font:12px monospace;color:var(--m);margin-top:4px" id=ldt></div>
  <div class=seg style="margin-top:10px"><button class=go onclick="A('iperf_on',{rate:+$('#rate').value,dur:+$('#dur').value,cls:$('#cls').value})">Start traffic</button><button onclick="A('iperf_off')">Stop traffic</button></div>
  <div class=f><div><span class=lab>Alerts to send</span><input type=number id=n value=100 min=1 max=5000></div><div><span class=lab>Gap between alerts (ms)</span><input type=number id=gap value=50 min=1 max=5000></div></div>
  <div class=chips><button onclick="$('#n').value=50">50</button><button onclick="$('#n').value=100">100</button><button onclick="$('#n').value=500">500</button><button onclick="$('#n').value=1000">1000</button></div>
  <div class=seg style="margin-top:10px"><button class=go onclick="A('bulk',{n:+$('#n').value,gap:+$('#gap').value/1000})">Send alerts</button></div></div>
</div>
<div class="row r2">
 <div class=c><h2>3 · Send an alert</h2>
  <div class=f><div><span class=lab>Type</span><select id=at><option>FIRE<option>MEDICAL<option>SECURITY<option>GENERAL</select></div>
  <div><span class=lab>Priority</span><select id=ap><option value=1>1 · high (emergency)<option value=2>2 · medium<option value=3>3 · low</select></div></div>
  <span class=lab>Message</span><input type=text id=am maxlength=200 placeholder="Fire on floor 3, use stairs B" onkeydown="if(event.key=='Enter')sendAlert()">
  <div class=seg style="margin-top:10px"><button class=go onclick="sendAlert()">Send to all subscribers</button></div><div id=sm style="font:12px monospace;color:var(--m);min-height:18px;margin-top:8px"></div>
  <span class=lab>Subscribers registered with the server</span><div class=chips id=subs></div></div>
 <div class=c><h2>Alert feed (newest first)</h2><div id=feed></div></div>
</div>
<div class=c><h2>Live traffic monitor (switch s1 counters, 1 s refresh)</h2><div class=kp id=nm></div><svg id=nh viewBox="0 0 680 190"></svg>
 <div class=leg><span><i style="background:var(--pr)"></i>direct link s1→s3 (Mbps)</span><span><i style="background:var(--dy)"></i>alternate path s1→s2 (Mbps)</span><span>dashed = 10 Mbps capacity</span></div></div>
<div class=c><h2>Overall delivery (server_log.csv)</h2><div class=kp id=k></div></div>
<div class="row r3" id=mc></div>
<div class="row r21">
 <div class=c><h2>Mode comparison chart</h2>
  <div class=tabs><button id=sl class=on onclick="SC('lin')">Linear scale</button><button id=sg onclick="SC('log')">Log scale (shows small values)</button></div>
  <svg id=cb viewBox="0 0 680 330"></svg>
  <div class=leg><span><i style="background:var(--pr)"></i>average ms</span><span><i style="background:var(--p95)"></i>p95 ms (slowest 5%)</span><span><i style="background:var(--mx)"></i>max ms</span></div></div>
 <div class=c><h2>Comparison table</h2><div style="overflow-x:auto"><table id=cmp></table></div>
  <div style="font-size:12px;color:var(--m);margin-top:10px;line-height:1.5">Alerts are sorted by the mode that was active when they were sent. Change mode with the buttons, then send alerts. Lower is better.</div></div>
</div>
<div class="row r2">
 <div class=c><h2>ACK round-trip, last 80 deliveries (ms)</h2><svg id=ch viewBox="0 0 680 240"></svg></div>
 <div class=c><h2>Client latency (client_log_*.csv)</h2><div class=kp id=l></div><h2 style="margin-top:14px">Latest deliveries</h2><div style="overflow-x:auto"><table id=t></table></div></div>
</div>
<script>
const $=s=>document.querySelector(s),MS=['static','priority','dynamic'],COL={static:'var(--st)',priority:'var(--pr)',dynamic:'var(--dy)'},f=v=>v==null?'–':(+v).toFixed(2).replace(/\.?0+$/,'');
let scale='lin',last=null;
/* perf: only touch the DOM when the markup really changed */
(()=>{const d=Object.getOwnPropertyDescriptor(Element.prototype,'innerHTML');document.querySelectorAll('[id]').forEach(e=>{let c;Object.defineProperty(e,'innerHTML',{get(){return d.get.call(this)},set(v){if(v!==c){c=v;d.set.call(this,v)}}})})})();
/* perf: never repaint the page while the user is scrolling */
let scT=0,pend=null,scTimer;
addEventListener('scroll',()=>{document.body.classList.add('scrolling');scT=Date.now();clearTimeout(scTimer);scTimer=setTimeout(()=>{document.body.classList.remove('scrolling');if(pend){const p=pend;pend=null;paint(p)}},150)},{passive:true});
async function A(a,v){$('#msg').textContent='...';const r=await fetch('/api/act',{method:'POST',body:JSON.stringify({a,v})});$('#msg').textContent=a+': '+(await r.json()).result;tick()}
function SC(m){scale=m;$('#sl').classList.toggle('on',m=='lin');$('#sg').classList.toggle('on',m=='log');if(last)bars(last)}
$('#chips').innerHTML='<span class=lab style="margin:0 6px 0 0">presets</span>'+[2,5,10,20,50,100,200].map(r=>`<button onclick="$('#rate').value=${r};ld()">${r} Mbps</button>`).join('');
function ld(){const r=+$('#rate').value||0,p=Math.min(100,r/10*100);$('#ld').style.width=p+'%';$('#ld').style.background=r>10?'var(--bad)':r>=7?'var(--p95)':'var(--dy)';$('#ldt').textContent=r+' Mbps offered into the 10 Mbps bottleneck = '+Math.round(r/10*100)+'% of link capacity'+(r>10?' (congested)':'')}
$('#rate').oninput=ld;ld();
function nice(m){const e=Math.pow(10,Math.floor(Math.log10(m))),x=m/e;return (x<=1?1:x<=2?2:x<=5?5:10)*e}
function bars(s){const bm=s.by_mode,W=680,H=330,L=62,R=14,T=14,B=44,pw=W-L-R,ph=H-T-B,met=[['avg','--pr'],['p95','--p95'],['max','--mx']];
 let vals=[];MS.forEach(m=>met.forEach(k=>{if(bm[m][k[0]]!=null)vals.push(bm[m][k[0]])}));
 if(!vals.length){$('#cb').innerHTML='<text x="'+W/2+'" y="'+H/2+'" text-anchor="middle" class=ax>No data yet: choose a mode and send alerts</text>';return}
 const mx=Math.max(...vals);let top,ticks=[],Y;
 if(scale=='lin'){top=nice(mx*1.1);for(let i=0;i<=5;i++)ticks.push(top*i/5);Y=v=>T+ph-(v/top)*ph}
 else{const hi=Math.pow(10,Math.ceil(Math.log10(mx*1.2))),lo=0.1;for(let v=lo;v<=hi*1.01;v*=10)ticks.push(v);Y=v=>T+ph-(Math.log10(Math.max(v,lo))-Math.log10(lo))/(Math.log10(hi)-Math.log10(lo))*ph}
 let o=ticks.map(t=>`<line class=g x1="${L}" x2="${W-R}" y1="${Y(t)}" y2="${Y(t)}"/><text class=ax x="${L-8}" y="${Y(t)+4}" text-anchor="end">${f(t)}</text>`).join('');
 const gw=pw/3,bw=Math.min(46,gw/4.2);
 MS.forEach((m,i)=>{const cx=L+gw*i+gw/2;met.forEach((k,j)=>{const v=bm[m][k[0]],x=cx+(j-1)*(bw+8)-bw/2;if(v==null)return;const y=Y(v),h=Math.max(2,T+ph-y);
  o+=`<rect x="${x}" y="${T+ph-h}" width="${bw}" height="${h}" rx="4" fill="var(${k[1]})"/><text x="${x+bw/2}" y="${T+ph-h-6}" text-anchor="middle" style="font-size:11px;font-weight:700">${f(v)}</text>`});
  o+=`<text x="${cx}" y="${H-16}" text-anchor="middle" style="font-size:13px;font-weight:700;fill:${COL[m]}">${m}</text>`});
 o+=`<text class=ax x="14" y="${T+ph/2}" transform="rotate(-90 14 ${T+ph/2})" text-anchor="middle">milliseconds</text>`;$('#cb').innerHTML=o}
function line(s){const d=s.rtt_recent,W=680,H=240,L=54,R=12,T=14,B=30,pw=W-L-R,ph=H-T-B;if(!d.length){$('#ch').innerHTML='<text x="340" y="120" text-anchor="middle" class=ax>No data yet</text>';return}
 const top=nice(Math.max(...d)*1.1||1),Y=v=>T+ph-(v/top)*ph,X=i=>L+(d.length<2?0:i/(d.length-1))*pw;let o='';
 for(let i=0;i<=4;i++){const v=top*i/4;o+=`<line class=g x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}"/><text class=ax x="${L-8}" y="${Y(v)+4}" text-anchor="end">${f(v)}</text>`}
 const pts=d.map((v,i)=>X(i)+','+Y(v)).join(' ');o+=`<polygon points="${X(0)},${T+ph} ${pts} ${X(d.length-1)},${T+ph}" fill="var(--em)" opacity=".12"/><polyline points="${pts}" fill="none" stroke="var(--em)" stroke-width="2" stroke-linejoin="round"/>`;
 const mi=d.indexOf(Math.max(...d));o+=`<circle cx="${X(mi)}" cy="${Y(d[mi])}" r="4" fill="var(--em)"/><text x="${Math.min(W-R-40,Math.max(L+20,X(mi)))}" y="${Y(d[mi])-8}" text-anchor="middle" style="font-weight:700">${f(d[mi])} ms</text>`;
 o+=`<text class=ax x="${L}" y="${H-8}">older</text><text class=ax x="${W-R}" y="${H-8}" text-anchor="end">newest</text>`;$('#ch').innerHTML=o}
async function sendAlert(){const m=$('#am').value.trim();if(!m){$('#sm').textContent='Write a message first';return}
await A('send',{type:$('#at').value,prio:$('#ap').value,msg:m});$('#sm').textContent=$('#msg').textContent;$('#am').value=''}
function extra(s){const n=s.net||{},K=(a,b)=>`<div class=k><b>${a}</b><small>${b}</small></div>`;
$('#nm').innerHTML=n.ok?K(f(n.direct)+' Mbps','direct link s1→s3')+K(f(n.alt)+' Mbps','alternate path s1→s2')+K(f(n.drops)+'/s','packets dropped on direct link')+K(Math.round(n.em_pps)+'/s','emergency class (UDP 5000)')+K(Math.round(n.be_pps)+'/s','normal class'):'<small>No switch data. Start Mininet and the controller, and run this dashboard with sudo.</small>';
const h=s.hist||[],W=680,H=190,L=46,R=10,T=10,B=22,top=Math.max(10,...h.map(x=>Math.max(x[0],x[1])))*1.1,Y=v=>T+(H-T-B)*(1-v/top),X=i=>L+i/Math.max(1,59)*(W-L-R);
let o=[0,.5,1].map(q=>`<text class=ax x="${L-6}" y="${Y(top*q/1.1)+4}" text-anchor="end">${f(top*q/1.1)}</text>`).join('')+`<line x1="${L}" x2="${W-R}" y1="${Y(10)}" y2="${Y(10)}" stroke="var(--m)" stroke-dasharray="4 4"/>`;
[[0,'--pr'],[1,'--dy']].forEach(([k,c])=>{if(h.length>1)o+=`<polyline fill="none" stroke="var(${c})" stroke-width="2" points="${h.map((x,i)=>X(i+60-h.length)+','+Y(x[k])).join(' ')}"/>`});
$('#nh').innerHTML=o;
$('#subs').innerHTML=(s.clients||[]).length?s.clients.map(c=>`<button disabled>${c}</button>`).join(''):'<small style="color:var(--m)">none yet. Run python3 client.py 10.0.0.1 on a host.</small>';
$('#feed').innerHTML=(s.feed||[]).map(a=>`<div class=fd><b class="p${a.prio}">${a.type} P${a.prio}</b><span>${a.msg.replace(/[<>&]/g,'')}</span><small>#${a.seq} ${new Date(a.t*1000).toLocaleTimeString()}</small></div>`).join('')||'<small style="color:var(--m)">No alerts yet.</small>'}
let lastSent=0,aliveUntil=0;
function topo(s){const st=s.state,down=st.link==='down',dyn=st.mode==='dynamic',net=s.net||{},rer=!!(net.ok&&net.alt>0.5);
 if(s.sent>lastSent)aliveUntil=Date.now()+2500;lastSent=s.sent;const busy=Date.now()<aliveUntil;
 let a,ac,altA=false;
 if(!down){a='ALERTS \u2192 direct path s1-s3';ac='g'}
 else if(dyn){a='ALERTS \u2192 detour via s2 (failover)';ac='o';altA=true}
 else{a='LINK DOWN \u2192 alerts are LOST ('+st.mode+' mode has no failover)';ac='r'}
 let b,bc,altB=false;
 if(!st.iperf){b='background traffic: off';bc=''}
 else if(down&&dyn){b='BACKGROUND \u2192 detour via s2';bc='o';altB=true}
 else if(down){b='BACKGROUND \u2192 blocked (link down)';bc='r'}
 else if(dyn&&rer){b='BACKGROUND \u2192 detour via s2 (rerouted)';bc='o';altB=true}
 else{b='BACKGROUND \u2192 direct path s1-s3';bc='g'}
 const altOn=altA||altB,active=st.iperf||busy;
 $('#pa').className='pill '+ac;$('#pa').textContent=a;$('#pb').className='pill '+bc;$('#pb').textContent=b;
 $('#dl').setAttribute('class',down?'down':'up'+((!altB&&active)?' flow':''));
 $('#al').setAttribute('class',altOn?'on'+(active?' flow':''):'');
 $('#ns2').setAttribute('class','nb'+(altOn?' hot':''));
 $('#xm').setAttribute('class',down?'on':'');
 $('#bdown').className='dg'+(down?' rd':'');$('#bup').className=down?'':'gn'}
async function tick(){try{const s=await (await fetch('/api/stats')).json();if(Date.now()-scT<150)pend=s;else{pend=null;paint(s)}}catch(e){}}
function paint(s){last=s;const bm=s.by_mode;topo(s);
$('#modes').innerHTML=MS.map(m=>`<button class="${s.state.mode==m?'on':''}" onclick="A('mode','${m}')">${m}</button>`).join('');
const K=(a,b)=>`<div class=k><b>${a}</b><small>${b}</small></div>`;
$('#k').innerHTML=K(s.sent,'deliveries attempted')+K(s.acked,'ACKed')+K(s.retried,'retransmitted')+K(s.unreachable,'unreachable')+K(s.loss+'%','loss after retries')+K(s.first_try_loss+'%','first-try loss')+K(f(s.rtt_avg)+' ms','avg ACK round-trip');
$('#l').innerHTML=s.lat?K(f(s.lat.avg),'avg ms')+K(f(s.lat.median),'median')+K(f(s.lat.p95),'p95')+K(f(s.lat.max),'max'):'<small>no client logs yet</small>';
const have=MS.filter(m=>bm[m].avg!=null),best=have.length?have.reduce((a,b)=>bm[a].avg<=bm[b].avg?a:b):null,sa=bm.static.avg;
$('#mc').innerHTML=MS.map(m=>{const x=bm[m],sp=(sa&&x.avg&&m!='static')?(sa/x.avg):null;return `<div class="mc ${m.slice(0,2)}">${m==best&&have.length>1?'<span class=badge>FASTEST</span>':''}<h3 style="color:${COL[m]}">${m}</h3><div class=big>${x.avg!=null?f(x.avg):'–'} <small>ms avg</small></div><div class=sub>p95 ${f(x.p95)} ms · max ${f(x.max)} ms<br>${x.sent} sent · ${x.unreachable} lost · loss ${f(x.loss)}%${sp?'<br><b style="color:var(--dy)">'+sp.toFixed(sp<10?1:0)+'× faster than static</b>':''}</div></div>`}).join('');
$('#cmp').innerHTML='<tr><th>mode<th>sent<th>retx<th>lost<th>loss %<th>avg<th>p95<th>max</tr>'+MS.map(m=>{const x=bm[m];return `<tr><td><b style="color:${COL[m]}">${m}</b><td>${x.sent}<td>${x.retried}<td>${x.unreachable}<td>${f(x.loss)}<td>${f(x.avg)}<td>${f(x.p95)}<td>${f(x.max)}</tr>`}).join('');
bars(s);line(s);extra(s);
$('#t').innerHTML='<tr><th>seq<th>type<th>client<th>rtt<th>try<th>status</tr>'+s.recent.map(r=>`<tr class="${r.status!='ACKED'?'bad':''}"><td>${r.seq}<td>${r.type}<td>${r.client}<td>${r.rtt_ms}<td>${r.attempts}<td>${r.status}</tr>`).join('')}
(async function loop(){if(!document.hidden)await tick();setTimeout(loop,1000)})();</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def _send(self, body, ctype="application/json"):
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/stats":
            self._send(json.dumps(stats()))
        else:
            self._send(PAGE, "text/html; charset=utf-8")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        d = json.loads(self.rfile.read(n) or b"{}")
        self._send(json.dumps({"result": act(d.get("a"), d.get("v")) or "ok"}))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("note: run with sudo so mnexec / ip link work")
    print(f"dashboard on http://{lan_ip()}:8000  (open this from any laptop on the same network; project root: {ROOT})")
    threading.Thread(target=monitor, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()
