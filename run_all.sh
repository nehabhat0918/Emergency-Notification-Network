#!/usr/bin/env bash

cd "$(dirname "$0")" || exit 1

WHAT="${1:-demo}"

RYUENV="${RYUENV:-$([ -f "$HOME/ryu-env/bin/activate" ] && echo "$HOME/ryu-env" || echo "$HOME/ryuenv")}"

[ -f "$RYUENV/bin/activate" ] || {
    echo "Ryu environment not found at $RYUENV (see the install steps)"
    exit 1
}

mkdir -p results

cleanup() {

    echo
    echo "== stopping everything =="

    [ -n "$RYU" ] && kill "$RYU" 2>/dev/null

    sudo pkill -f dashboard/app.py 2>/dev/null

    sudo pkill -f server.py 2>/dev/null

    sudo pkill -f client.py 2>/dev/null

    sudo pkill iperf 2>/dev/null

    sudo mn -c >/dev/null 2>&1
}

trap cleanup EXIT

sudo -v || exit 1

echo "[1/4] cleaning old Mininet state"

sudo mn -c >/dev/null 2>&1

sudo pkill -f server.py 2>/dev/null

sudo pkill -f client.py 2>/dev/null

sudo pkill -f dashboard/app.py 2>/dev/null

echo "[2/4] starting Ryu controller (log: results/ryu.log)"

source "$RYUENV/bin/activate"

SDN_MODE="${MODE:-dynamic}" ryu-manager --ofp-tcp-listen-port 6653 controller/emergency_controller.py > results/ryu.log 2>&1 &

RYU=$!

deactivate 2>/dev/null

for i in $(seq 1 30); do

    ss -ltn 2>/dev/null | grep -q ':6653 ' && break

    kill -0 "$RYU" 2>/dev/null || {
        echo "Ryu stopped. Last lines:"
        tail -n 15 results/ryu.log
        exit 1
    }

    sleep 1

done

ss -ltn 2>/dev/null | grep -q ':6653 ' || {
    echo "Ryu never started listening on :6653"
    tail -n 15 results/ryu.log
    exit 1
}

echo "controller is up"

case "$WHAT" in

    quick|full)

        echo "[3/4] running automatic experiments"

        if [ "$WHAT" = quick ]; then

            sudo -E python3 -u experiments/run_experiments.py --quick

        else

            sudo -E python3 -u experiments/run_experiments.py --reps 3

        fi

        echo "[4/4] results"

        cat "results/$WHAT/summary.md" 2>/dev/null

        echo "Charts + CSV: results/$WHAT/"

        ;;

    *)

        echo "[3/4] starting dashboard -> open http://localhost:8000 in Firefox"

        rm -f server_log.csv client_log_*.csv

        sudo python3 dashboard/app.py > results/dashboard.log 2>&1 &

        echo "[4/4] starting network + server + clients (windows will open)"

        sudo -E python3 demo_launcher.py --mode "${MODE:-dynamic}"

        ;;

esac
