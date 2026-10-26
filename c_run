#!/usr/bin/env bash
# Person C - Steps 2 and 5: start the topology and capture evidence.
# Run from the project root (the folder that contains topo/ and qos/).
#
#   bash c_run.sh clean      # wipe old Mininet/OVS state (do this before every run)
#   bash c_run.sh topo       # start topology (opens the mininet> prompt)
#   bash c_run.sh qos        # show queue/tc state on s1-eth3 (run in 2nd terminal while topo is up)
#   bash c_run.sh flows      # dump OpenFlow flows on s1 (2nd terminal)
#   bash c_run.sh capture N  # tcpdump on s1-eth3 for N seconds -> results/capture_*.pcap
#
# Edit these if your repo uses different names:
TOPO=topo/emergency_topo.py
BOTTLENECK_IF=s1-eth3
SWITCH=s1

cmd="${1:-help}"
mkdir -p results

case "$cmd" in
  clean)
    sudo mn -c
    ;;
  topo)
    sudo mn -c >/dev/null 2>&1 || true
    sudo python3 "$TOPO"
    # Inside the mininet> prompt, type:
    #   pingall
    #   h1 ping -c 3 h2
    #   links
    ;;
  qos)
    if [ -x qos/show ]; then
      sudo qos/show "$BOTTLENECK_IF"
    else
      echo "== tc qdisc =="; sudo tc -s qdisc show dev "$BOTTLENECK_IF"
      echo "== tc class =="; sudo tc -s class show dev "$BOTTLENECK_IF"
      echo "== OVS qos/queue =="; sudo ovs-vsctl list qos; sudo ovs-vsctl list queue
    fi
    ;;
  flows)
    sudo ovs-ofctl -O OpenFlow13 dump-flows "$SWITCH"
    ;;
  capture)
    secs="${2:-30}"
    out="results/capture_$(date +%H%M%S).pcap"
    echo "Capturing ${secs}s on $BOTTLENECK_IF -> $out"
    sudo timeout "$secs" tcpdump -i "$BOTTLENECK_IF" -w "$out" || true
    echo "saved $out"
    ;;
  *)
    sed -n '2,12p' "$0"
    ;;
esac
