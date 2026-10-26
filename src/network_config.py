"""
network_config.py  --  shared constants (simple version).

Used by the controller, the topology script and ctl_send.py.
If the topology or ports change, change them HERE only.

Topology:

   h1 (server), h5 (bg source)            h2, h3, h4 (clients), h6 (bg sink)
              \\                                    /
               [s1] ===== direct link (bottleneck) ===== [s3]
                  \\                                    /
                   +-------------- [s2] ---------------+
                         alternate path s1-s2-s3
"""

# ---------------------------------------------------------------- application
SERVER_IP = '10.0.0.1'
SERVER_PORT = 5000             # emergency traffic: the server listens here
CLIENT_PORT = 6000             # all clients bind this local port (never matched by the controller)
IPERF_PORT = 5001              # background iperf traffic (NOT emergency)
CTL_PORT = 9998                # app -> controller event channel (UDP -> packet-in)
CTL_IP = '10.0.0.100'          # virtual IP the server sends events to
CTL_MAC = '00:00:00:00:00:64'  # static ARP entry on h1 for CTL_IP

# ------------------------------------------------------------------- topology
# host name : (ip, dpid of its switch, port on that switch)
HOSTS = {
    'h1': ('10.0.0.1', 1, 1),   # server
    'h5': ('10.0.0.5', 1, 2),   # background source
    'h2': ('10.0.0.2', 3, 1),   # client
    'h3': ('10.0.0.3', 3, 2),   # client
    'h4': ('10.0.0.4', 3, 3),   # client
    'h6': ('10.0.0.6', 3, 4),   # background sink
}
HOST_BY_IP = {ip: (dpid, port) for ip, dpid, port in HOSTS.values()}

# switch link ports, used by the topology script: (a, b) -> (port on a, port on b)
LINKS = {
    (1, 3): (3, 5),   # direct link  s1-eth3 <-> s3-eth5   (the bottleneck)
    (1, 2): (4, 1),   # alternate    s1-eth4 <-> s2-eth1
    (2, 3): (2, 6),   # alternate    s2-eth2 <-> s3-eth6
}

# Which port should switch X use to reach a host that sits on switch Y?
# NEXT_PORT[X][Y].  Two versions: the direct path and the alternate path via s2.
NEXT_PORT_DIRECT = {1: {3: 3},
                    2: {1: 1, 3: 2},
                    3: {1: 5}}
NEXT_PORT_ALT = {1: {3: 4},       # s1 -> s2 -> s3
                 2: {1: 1, 3: 2},
                 3: {1: 6}}       # s3 -> s2 -> s1

BOTTLE_DPID, BOTTLE_PORT = 1, 3   # port we watch for congestion (s1 towards s3)

# ------------------------------------------------------------------------ QoS
QOS_RATE = 10_000_000          # 10 Mbps on every switch-to-switch port
Q_BEST, Q_EMERGENCY = 0, 1     # OVS queue ids
Q_BEST_MIN = 1_000_000         # guaranteed rate, normal queue
Q_EMERGENCY_MIN = 6_000_000    # guaranteed rate, emergency queue

# ---------------------------------------------------------- controller policy
MODES = ('static', 'priority', 'dynamic')
POLL_INTERVAL = 2.0            # seconds between port-stat polls
CONGEST_MBPS = 7.0             # at or above this on the bottleneck = congested
REROUTE_HOLD = 15.0            # keep best-effort traffic rerouted this many seconds


def interswitch_ports():
    """Yield (dpid, port) for every switch-to-switch port (used to set up QoS)."""
    for (a, b), (pa, pb) in LINKS.items():
        yield a, pa
        yield b, pb
