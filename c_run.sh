#!/usr/bin/env bash

TOPO=topo/b_test_topo.py
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
        ;;

    qos)
        if [ -x qos/show ]; then
            sudo qos/show "$BOTTLENECK_IF"
        else
            echo "== tc qdisc =="

            sudo tc -s qdisc show dev "$BOTTLENECK_IF"

            echo "== tc class =="

            sudo tc -s class show dev "$BOTTLENECK_IF"

            echo "== OVS qos/queue =="

            sudo ovs-vsctl list qos

            sudo ovs-vsctl list queue
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
