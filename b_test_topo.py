#!/usr/bin/env python3
"""
b_test_topo.py  --  TEMPORARY Mininet topology so Person B can test the controller
before Person C's topology is ready. Person C owns the real topology file; do not
commit this over it (keep it local or name it differently).

    sudo python3 topo/b_test_topo.py               # build network, open Mininet CLI
    sudo python3 topo/b_test_topo.py --no-cli      # build, check connectivity, exit

Needs the Ryu controller already running:   ryu-manager controller/emergency_controller.py

What this script sets up
  * 3 OVS switches (OpenFlow 1.3, fail-mode secure) + 6 hosts (layout in src/network_config.py)
  * Explicit port numbers so the controller knows which port leads where
  * 10 Mbps htb QoS with 2 queues on every inter-switch port (queue 1 = emergency)
  * Static ARP on every host (no ARP flooding -> no loops in the triangle topology),
    plus an ARP entry on h1 for the virtual controller IP used by the app event channel
"""
import argparse
import os
import subprocess
import sys
import time
from functools import partial

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import network_config as cfg  # noqa: E402

from mininet.cli import CLI            # noqa: E402
from mininet.log import info, setLogLevel  # noqa: E402
from mininet.net import Mininet        # noqa: E402
from mininet.node import OVSSwitch, RemoteController  # noqa: E402


def sh(cmd):
    """Run a root-namespace shell command (OVS and tc live in the root namespace)."""
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True)


def clear_qos():
    sh('ovs-vsctl -- --all destroy QoS -- --all destroy Queue')


def setup_qos():
    """Attach a linux-htb QoS (10 Mbps, queue0 best-effort, queue1 emergency) to all inter-switch ports."""
    clear_qos()
    for dpid, port in cfg.interswitch_ports():
        intf = f's{dpid}-eth{port}'
        cmd = (f'ovs-vsctl -- set port {intf} qos=@q '
               f'-- --id=@q create qos type=linux-htb other-config:max-rate={cfg.QOS_RATE} '
               f'queues:{cfg.Q_BEST}=@q0 queues:{cfg.Q_EMERGENCY}=@q1 '
               f'-- --id=@q0 create queue other-config:min-rate={cfg.Q_BEST_MIN} '
               f'other-config:max-rate={cfg.QOS_RATE} other-config:priority=1 '
               f'-- --id=@q1 create queue other-config:min-rate={cfg.Q_EMERGENCY_MIN} '
               f'other-config:max-rate={cfg.QOS_RATE} other-config:priority=0')
        r = sh(cmd)
        if r.returncode != 0:
            info(f'*** QoS setup failed on {intf}: {r.stdout}\n')


def make_network(controller_ip='127.0.0.1', controller_port=6653):
    """Build and start the network. Returns the started Mininet object."""
    clear_qos()
    net = Mininet(controller=None,
                  switch=partial(OVSSwitch, protocols='OpenFlow13', failMode='secure'),
                  autoSetMacs=False)
    net.addController('c0', controller=RemoteController, ip=controller_ip, port=controller_port)

    switches = {i: net.addSwitch(f's{i}', dpid=f'{i:016x}') for i in (1, 2, 3)}
    for n, name in enumerate(sorted(cfg.HOSTS), start=1):
        ip, dpid, port = cfg.HOSTS[name]
        host_no = int(name[1:])
        h = net.addHost(name, ip=f'{ip}/24', mac=f'00:00:00:00:00:{host_no:02x}')
        net.addLink(h, switches[dpid], port1=0, port2=port)
    for (a, b), (pa, pb) in cfg.LINKS.items():
        net.addLink(switches[a], switches[b], port1=pa, port2=pb)

    net.start()
    if hasattr(net, 'waitConnected'):
        if not net.waitConnected(timeout=15):
            info('*** WARNING: switches did not connect - is ryu-manager running?\n')
    setup_qos()
    net.staticArp()
    net.get('h1').cmd(f'arp -s {cfg.CTL_IP} {cfg.CTL_MAC}')
    time.sleep(1.0)   # let the controller finish installing flows
    return net


def stop_network(net):
    net.stop()
    clear_qos()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--controller-ip', default='127.0.0.1')
    ap.add_argument('--controller-port', type=int, default=6653)
    ap.add_argument('--no-cli', action='store_true')
    a = ap.parse_args()

    setLogLevel('info')
    net = make_network(a.controller_ip, a.controller_port)
    try:
        info('*** Connectivity check\n')
        loss = net.pingAll()
        if a.no_cli:
            return
        info('\n*** Useful commands inside the CLI:\n'
             '  xterm h1 h2 h3 h4                       open terminals\n'
             '  h1 python3 src/ctl_send.py register 10.0.0.2   fake app event (no server needed)\n'
             '  h6 iperf -s -u &   /   h5 iperf -u -c 10.0.0.6 -b 20M -t 30 &   congestion (iperf uses port 5001)\n'
             '  h1 python3 src/ctl_send.py mode static|priority|dynamic\n'
             '  sh ovs-ofctl -O OpenFlow13 dump-flows s1\n'
             '  link s1 s3 down  /  link s1 s3 up       link failure\n\n')
        CLI(net)
    finally:
        stop_network(net)


if __name__ == '__main__':
    main()
