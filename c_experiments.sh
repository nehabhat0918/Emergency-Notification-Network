#!/usr/bin/env bash

EXPERIMENT=experiments/run_experiments.py

set -e

[ -f "$EXPERIMENT" ] || {
    echo "Cannot find $EXPERIMENT - edit the EXPERIMENT variable"
    exit 1
}

if ! (ss -ltn 2>/dev/null | grep -q ':6653'); then
    echo "WARNING: nothing is listening on :6653. Start the Ryu controller first (Person B)."
fi

echo "=== 1) quick run ==="

sudo python3 "$EXPERIMENT" --quick

echo "=== 2) full run (3 repetitions) ==="

sudo python3 "$EXPERIMENT" --reps 3

echo "=== 3) package results for A and B ==="

sudo chown -R "$USER":"$USER" results 2>/dev/null || true

zip -r results_$(date +%Y%m%d_%H%M).zip results/

ls -lh results_*.zip | tail -1

echo

echo "SANITY CHECK (do this by eye in results/full/summary.md):"

echo "  congestion: static has the highest latency; priority and dynamic are lower"

echo "  linkfail  : static and priority show loss during the outage; dynamic shows ~0 and recovers"

echo "  loss      : first-try loss is about 10% in every mode; loss after retries stays near 0 (ACK + retransmit works)"

echo "If a result looks wrong, send results/ to Person B before rerunning."
