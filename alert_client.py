#!/usr/bin/env python3
"""Emergency Notification Network - UDP subscriber client (Part A).

Registers with the server, ACKs every ALERT, ignores duplicate retransmits
(but still ACKs them), and logs one-way latency to CSV.
Usage: python3 alert_client.py <server_ip> [--port 5000] [--log client_h2.csv]
"""
import argparse, csv, json, os, random, socket, time

NAMES = {1: "CRITICAL", 2: "WARNING", 3: "INFO"}


def main():
    ap = argparse.ArgumentParser(description="UDP emergency alert client")
    ap.add_argument("server_ip")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--bind-port", type=int, default=0, help="fixed local port (0 = any)")
    ap.add_argument("--log", default="client_log.csv")
    ap.add_argument("--drop-prob", type=float, default=0.0,
                    help="testing only: ignore this fraction of incoming alerts")
    ap.add_argument("--count", type=int, default=0, help="exit after N unique alerts")
    a = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if a.bind_port:
        sock.bind(("0.0.0.0", a.bind_port))
    server = (a.server_ip, a.port)

    # registration with retry
    sock.settimeout(1.0)
    ok = False
    for _ in range(5):
        sock.sendto(json.dumps({"type": "REGISTER"}).encode(), server)
        try:
            while True:
                data, _ = sock.recvfrom(2048)
                if json.loads(data.decode()).get("type") == "REGISTERED":
                    ok = True
                    break
        except (socket.timeout, ValueError):
            pass
        if ok:
            break
    print("Registered, waiting for alerts..." if ok else
          "[warn] no REGISTERED reply; continuing anyway")
    sock.settimeout(None)

    new = not os.path.exists(a.log)
    f = open(a.log, "a", newline="")
    w = csv.writer(f)
    if new:
        w.writerow(["recv_ts", "seq", "level", "send_ts", "one_way_ms", "retry", "duplicate"])
    seen, unique = set(), 0
    try:
        while True:
            data, _ = sock.recvfrom(2048)
            try:
                msg = json.loads(data.decode())
            except (ValueError, UnicodeDecodeError):
                print("[warn] malformed packet ignored")
                continue
            if msg.get("type") != "ALERT" or "seq_no" not in msg:
                continue
            if a.drop_prob and random.random() < a.drop_prob:
                continue  # simulated loss
            now = time.time()
            seq = msg["seq_no"]
            sock.sendto(json.dumps({"type": "ACK", "seq_no": seq,
                                    "client_ts": now}).encode(), server)
            dup = seq in seen
            seen.add(seq)
            lat = (now - msg.get("timestamp", now)) * 1000
            lvl = msg.get("priority", 1)
            w.writerow(["%.6f" % now, seq, lvl, "%.6f" % msg.get("timestamp", now),
                        "%.3f" % lat, msg.get("retry", 0), int(dup)])
            f.flush()
            if dup:
                print("duplicate of #%d (re-ACKed)" % seq)
                continue
            unique += 1
            print("%s ALERT #%d: %s (%.2f ms, retry %d)"
                  % (NAMES.get(lvl, lvl), seq, msg.get("text", ""), lat, msg.get("retry", 0)))
            if a.count and unique >= a.count:
                break
    except KeyboardInterrupt:
        pass
    finally:
        sock.sendto(json.dumps({"type": "UNREGISTER"}).encode(), server)
        f.close()


if __name__ == "__main__":
    main()
