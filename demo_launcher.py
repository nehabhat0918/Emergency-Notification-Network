#!/usr/bin/env python3
"""
demo_launcher.py - builds the network, opens windows for the alert server (h1) and
clients (h2, h3, h4), then gives you the mininet> prompt.  Used by:  bash run_all.sh
Needs Ryu already running (run_all.sh does that).
"""
import argparse, importlib.util, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, 'src'))

from mininet.cli import CLI            # noqa: E402
from mininet.log import setLogLevel    # noqa: E402
from mininet.term import makeTerm      # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--mode', default='dynamic', choices=['static', 'priority', 'dynamic'])
ap.add_argument('--iperf', action='store_true', help='also start 20 Mbps background traffic')
ap.add_argument('--topo', default=os.path.join(ROOT, 'topo', 'emergency_topo.py'))
a = ap.parse_args()
if not os.path.exists(a.topo):
    a.topo = os.path.join(ROOT, 'topo', 'b_test_topo.py')

spec = importlib.util.spec_from_file_location('topo_mod', a.topo)
topo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(topo)

setLogLevel('info')
net = topo.make_network()
try:
    h1, h2, h3, h4, h5, h6 = (net.get(n) for n in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'))
    h1.cmd(f'python3 {ROOT}/src/ctl_send.py mode {a.mode}')
    makeTerm(h1, 'SERVER h1', cmd='python3 server.py')
    time.sleep(2)
    makeTerm(h2, 'CLIENT h2', cmd='python3 client.py 10.0.0.1')
    makeTerm(h3, 'CLIENT h3', cmd='python3 client.py 10.0.0.1')
    makeTerm(h4, 'CLIENT h4', cmd='python3 client.py 10.0.0.1')
    if a.iperf:
        h6.popen('iperf -s -u', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        h5.popen('iperf -u -c 10.0.0.6 -b 20M -t 600', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('\n=== READY ===')
    print(' server window : type   FIRE 1 fire in building')
    print(' dashboard     : http://localhost:8000')
    print(' congestion    : h6 iperf -s -u &   then   h5 iperf -u -c 10.0.0.6 -b 20M -t 90 &')
    print(' modes         : h1 python3 src/ctl_send.py mode static|priority|dynamic')
    print(' bulk test     : h1 python3 bulk.py 100 0.05      then   h1 python3 analyze.py')
    print(' type exit here to stop everything\n')
    CLI(net)
finally:
    subprocess.run(['pkill', '-f', 'server.py'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['pkill', '-f', 'client.py'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['pkill', 'iperf'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    topo.stop_network(net)
