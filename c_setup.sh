#!/usr/bin/env bash

set -e

sudo apt update

sudo apt install -y mininet openvswitch-switch openvswitch-testcontroller \
    python3 python3-pip tcpdump iperf iperf3 iproute2 net-tools git zip

pip3 install --break-system-packages matplotlib pandas numpy 2>/dev/null \
    || pip3 install matplotlib pandas numpy

sudo systemctl enable --now openvswitch-switch

echo "--- version checks ---"

ovs-vsctl --version | head -1

mn --version

sudo ovs-vsctl show

echo "--- Mininet self-test (should print 0% dropped) ---"

sudo mn -c >/dev/null 2>&1 || true

sudo mn --test pingall

echo "SETUP OK"
