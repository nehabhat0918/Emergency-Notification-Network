#!/usr/bin/env bash
# Person C - Step 9: run experiments (quick first, then full) and package results.
# Run from the project root:  bash c_experiments.sh
#
# Edit EXPERIMENT if your script has a different name/path.
EXPERIMENT=experiments/run_experiments.py

set -e
[ -f "$EXPERIMENT" ] || { echo "Cannot find $EXPERIMENT - edit the EXPERIMENT variable"; exit 1; }

# Make sure the Ryu controller is already running in another terminal:
#   ryu-manager controller/<your_controller>.py
if ! (ss -ltn 2>/dev/null | grep -q ':6653'); then
  echo "WARNING: nothing is listening on :6653. Start the Ryu controller first (Person B)."
fi

sudo mn -c >/dev/null 2>&1 || true

echo "=== 1) quick run ==="
sudo python3 "$EXPERIMENT" --quick

echo "=== 2) full run (3 repetitions) ==="
sudo mn -c >/dev/null 2>&1 || true
sudo python3 "$EXPERIMENT" --reps 3

echo "=== 3) package results for A and B ==="
sudo chown -R "$USER":"$USER" results 2>/dev/null || true
zip -r results_$(date +%Y%m%d_%H%M).zip results/
ls -lh results_*.zip | tail -1

echo
echo "SANITY CHECK (do this by eye in results/):"
echo "  static  mode -> should show packet loss under congestion / link failure"
echo "  dynamic mode -> should show ~0 loss"
echo "If not, send results/ to Person B to retune network_config.py, then rerun this script."
