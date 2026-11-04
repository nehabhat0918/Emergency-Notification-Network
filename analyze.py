import csv
import glob
import os
import statistics
import sys

d = sys.argv[1] if len(sys.argv) > 1 else "."

rows = list(
    csv.DictReader(
        open(os.path.join(d, "server_log.csv"))
    )
)

sent = len(rows)

if not sent:
    sys.exit("server_log.csv has no rows yet")

acked = [
    r for r in rows
    if r["status"] == "ACKED"
]

retried = [
    r for r in acked
    if int(r["attempts"]) > 1
]

lat = []

for f in glob.glob(os.path.join(d, "client_log_*.csv")):

    lat += [
        float(r["latency_ms"])
        for r in csv.DictReader(open(f))
    ]

print(f"deliveries attempted : {sent}")
print(f"ACKed                : {len(acked)}")
print(f"needed retransmit    : {len(retried)}")
print(f"unreachable          : {sent - len(acked)}")

print(
    f"loss after retries   : "
    f"{(sent - len(acked)) / sent * 100:.2f}%"
)

print(
    f"first-try loss       : "
    f"{len(retried) / sent * 100:.2f}%"
)

if lat:

    lat.sort()

    print(
        f"latency ms  avg={statistics.mean(lat):.3f}  "
        f"median={statistics.median(lat):.3f}  "
        f"p95={lat[max(0, int(len(lat)*0.95)-1)]:.3f}  "
        f"max={lat[-1]:.3f}"
    )
