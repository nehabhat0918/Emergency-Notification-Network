#!/usr/bin/env python3
"""
ctl_send.py  --  send a fake application event to the SDN controller (Person B's test tool).

Run it on h1 (switch s1 punts UDP port 9998 to the controller as a packet-in):

    mininet> h1 python3 src/ctl_send.py register 10.0.0.2     # subscriber joined
    mininet> h1 python3 src/ctl_send.py unregister 10.0.0.2
    mininet> h1 python3 src/ctl_send.py alert_start
    mininet> h1 python3 src/ctl_send.py alert_end
    mininet> h1 python3 src/ctl_send.py mode static|priority|dynamic
    mininet> h1 python3 src/ctl_send.py status                # controller logs its state

This lets you test the controller without waiting for Person A's server.
"""
import json
import socket
import sys

import network_config as cfg


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    kind = sys.argv[1]
    payload = {'type': kind}
    if kind in ('register', 'unregister'):
        if len(sys.argv) < 3:
            sys.exit('usage: ctl_send.py %s <client-ip>' % kind)
        payload['client'] = sys.argv[2]
    elif kind == 'mode':
        if len(sys.argv) < 3 or sys.argv[2] not in cfg.MODES:
            sys.exit('usage: ctl_send.py mode ' + '|'.join(cfg.MODES))
        payload['value'] = sys.argv[2]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(json.dumps(payload).encode('utf-8'), (cfg.CTL_IP, cfg.CTL_PORT))
    print('sent', payload)


if __name__ == '__main__':
    main()
