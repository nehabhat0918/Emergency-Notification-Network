#!/usr/bin/env python3
"""Compute latency and loss from the CSV logs.
Usage: python3 analyze_logs.py --server server_log.csv [--clients client_h2.csv client_h3.csv ...]
"""
import argparse, csv, statistics


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100.0 * (len(v) - 1))))] if v else float("nan")


def stats(name, v):
    if not v:
        print("  %-12s no data" % name)
        return
    print("  %-12s n=%-5d avg=%8.2f  min=%8.2f  p95=%8.2f  max=%8.2f ms"
          % (name, len(v), statistics.mean(v), min(v), pct(v, 95), max(v)))


ap = argparse.ArgumentParser()
ap.add_argument("--server", required=True)
ap.add_argument("--clients", nargs="*", default=[])
a = ap.parse_args()

rows = list(csv.DictReader(open(a.server)))
print("== Server log: %s ==" % a.server)
total = len(rows)
acked = [r for r in rows if r["status"] == "ACKED"]
failed = total - len(acked)
retx = sum(int(r["retries"]) for r in rows)
print("  deliveries: %d  acked: %d  failed: %d  loss after retries: %.2f%%  retransmissions: %d"
      % (total, len(acked), failed, 100.0 * failed / total if total else 0, retx))
stats("RTT all", [float(r["rtt_ms"]) for r in acked])
for lvl, name in ((1, "CRITICAL"), (2, "WARNING"), (3, "INFO")):
    sub = [r for r in rows if r["level"] == str(lvl)]
    if sub:
        ok = [float(r["rtt_ms"]) for r in sub if r["status"] == "ACKED"]
        first_try = sum(1 for r in sub if r["status"] == "ACKED" and r["retries"] == "0")
        print("  %s: %d deliveries, %.1f%% delivered first try" % (name, len(sub), 100.0 * first_try / len(sub)))
        stats("  RTT", ok)

for path in a.clients:
    c = [r for r in csv.DictReader(open(path)) if r["duplicate"] == "0"]
    print("== Client log: %s ==" % path)
    stats("one-way", [float(r["one_way_ms"]) for r in c])
