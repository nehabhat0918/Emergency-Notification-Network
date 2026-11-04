#!/usr/bin/env python3
"""Emergency Notification Network - UDP alert server (Part A).

Subscribers send REGISTER to the alert port. The operator (or --auto mode)
sends ALERTs, which are delivered to every subscriber as one UDP datagram
each ("multicast-style" unicast fan-out). Each ALERT must be ACKed; the
server retransmits on timeout and logs everything to CSV.

Message format (JSON, one datagram):
  {"type": "ALERT", "priority": 1-3, "seq_no": N, "timestamp": <float>,
   "text": "...", "retry": 0}
Other types: REGISTER, REGISTERED, UNREGISTER, ACK
"""
import argparse, csv, json, os, random, socket, threading, time

NAMES = {1: "CRITICAL", 2: "WARNING", 3: "INFO"}
LEVELS = {"1": 1, "critical": 1, "2": 2, "warning": 2, "3": 3, "info": 3}
TOS = {1: 0xB8, 2: 0x68, 3: 0x00}  # DSCP EF / AF31 / best effort (optional)


class AlertServer:
    def __init__(self, a):
        self.a = a
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", a.port))
        self.lock = threading.Lock()
        self.subs = set()
        self.pending = {}      # (addr, seq) -> dict
        self.fails = {}        # addr -> consecutive failed alerts
        self.seq = 0
        self.running = True
        self.stats = {"sent": 0, "acked": 0, "failed": 0, "retx": 0}
        new = not os.path.exists(a.log)
        self.logf = open(a.log, "a", newline="")
        self.log = csv.writer(self.logf)
        if new:
            self.log.writerow(["seq", "client", "level", "send_ts", "ack_ts",
                               "rtt_ms", "retries", "status"])

    # ---------- receiving ----------
    def listen(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
            except OSError:
                break
            try:
                msg = json.loads(data.decode())
                mtype = msg["type"]
            except (ValueError, KeyError, UnicodeDecodeError, TypeError):
                print("[warn] malformed packet from", addr)
                continue
            if mtype == "REGISTER":
                with self.lock:
                    new = addr not in self.subs
                    self.subs.add(addr)
                    self.fails[addr] = 0
                print(("registered " if new else "re-registered ") + "%s:%d" % addr)
                self.reply(addr, "REGISTERED")
            elif mtype == "UNREGISTER":
                with self.lock:
                    self.subs.discard(addr)
                    for k in [k for k in self.pending if k[0] == addr]:
                        del self.pending[k]
                print("unregistered %s:%d" % addr)
            elif mtype == "ACK":
                self.on_ack(addr, msg.get("seq_no"))

    def reply(self, addr, mtype):
        self.sock.sendto(json.dumps({"type": mtype, "timestamp": time.time()}).encode(), addr)

    def on_ack(self, addr, seq):
        now = time.time()
        with self.lock:
            p = self.pending.pop((addr, seq), None)
            if p is None:
                return  # duplicate or late ACK
            self.fails[addr] = 0
            self.stats["acked"] += 1
            self.log.writerow([seq, "%s:%d" % addr, p["level"], "%.6f" % p["first"],
                               "%.6f" % now, "%.3f" % ((now - p["first"]) * 1000),
                               p["tries"] - 1, "ACKED"])
            self.logf.flush()

    # ---------- sending / reliability ----------
    def transmit(self, addr, seq, level, text, first, retry):
        pkt = json.dumps({"type": "ALERT", "priority": level, "seq_no": seq,
                          "timestamp": first, "text": text, "retry": retry}).encode()
        if self.a.dscp:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, TOS[level])
        self.sock.sendto(pkt, addr)

    def send_alert(self, level, text):
        with self.lock:
            self.seq += 1
            seq = self.seq
            targets = list(self.subs)
            now = time.time()
            for addr in targets:
                self.pending[(addr, seq)] = {"first": now, "last": now, "tries": 1,
                                             "level": level, "text": text}
                self.stats["sent"] += 1
        for addr in targets:
            self.transmit(addr, seq, level, text, now, 0)
        print("sent %s alert #%d to %d clients" % (NAMES[level], seq, len(targets)))

    def retransmit_loop(self):
        while self.running:
            time.sleep(0.05)
            now = time.time()
            resend, dead = [], []
            with self.lock:
                for (addr, seq), p in list(self.pending.items()):
                    if now - p["last"] < self.a.timeout:
                        continue
                    if p["tries"] > self.a.retries:       # 1 send + N retries
                        del self.pending[(addr, seq)]
                        self.stats["failed"] += 1
                        self.log.writerow([seq, "%s:%d" % addr, p["level"],
                                           "%.6f" % p["first"], "", "", p["tries"] - 1, "FAILED"])
                        self.logf.flush()
                        self.fails[addr] = self.fails.get(addr, 0) + 1
                        if self.fails[addr] >= self.a.max_fail and addr in self.subs:
                            self.subs.discard(addr)
                            dead.append(addr)
                    else:
                        p["tries"] += 1
                        p["last"] = now
                        self.stats["retx"] += 1
                        resend.append((addr, seq, p["level"], p["text"], p["first"], p["tries"] - 1))
            for r in resend:
                self.transmit(*r)
            for addr in dead:
                print("removed unresponsive client %s:%d" % addr)

    # ---------- run ----------
    def wait_drain(self):
        deadline = time.time() + self.a.timeout * (self.a.retries + 2)
        while self.pending and time.time() < deadline:
            time.sleep(0.1)

    def summary(self):
        s = self.stats
        total = s["sent"] or 1
        print("\n--- summary ---")
        print("alerts sent: %d  acked: %d  failed: %d  retransmissions: %d  delivery: %.1f%%"
              % (s["sent"], s["acked"], s["failed"], s["retx"], 100.0 * s["acked"] / total))

    def run(self):
        threading.Thread(target=self.listen, daemon=True).start()
        threading.Thread(target=self.retransmit_loop, daemon=True).start()
        print("Alert server on UDP port %d  (timeout %.2fs, retries %d)"
              % (self.a.port, self.a.timeout, self.a.retries))
        try:
            if self.a.auto:
                self.auto_mode()
            else:
                self.console()
        except KeyboardInterrupt:
            pass
        self.wait_drain()
        self.summary()
        self.running = False
        self.logf.close()

    def auto_mode(self):
        print("waiting %.1fs for clients to register..." % self.a.wait)
        time.sleep(self.a.wait)
        for i in range(self.a.auto):
            lvl = self.a.level if self.a.level else random.choice([1, 2, 3])
            self.send_alert(lvl, "auto alert %d" % (i + 1))
            time.sleep(self.a.interval)

    def console(self):
        print("Type: <level> <text>   level = 1/critical, 2/warning, 3/info (default 1)")
        print("Commands: list | quit")
        while True:
            try:
                line = input("alert> ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line == "quit":
                break
            if line == "list":
                with self.lock:
                    print("subscribers:", sorted("%s:%d" % s for s in self.subs) or "none")
                continue
            head, _, rest = line.partition(" ")
            if head.lower() in LEVELS and rest:
                self.send_alert(LEVELS[head.lower()], rest)
            else:
                self.send_alert(1, line)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="UDP emergency alert server")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--timeout", type=float, default=0.5, help="ACK timeout (s)")
    ap.add_argument("--retries", type=int, default=3, help="max retransmissions")
    ap.add_argument("--max-fail", type=int, default=3,
                    help="remove client after N consecutive failed alerts")
    ap.add_argument("--log", default="server_log.csv")
    ap.add_argument("--dscp", action="store_true", help="mark IP TOS by priority")
    ap.add_argument("--auto", type=int, default=0, help="send N alerts automatically")
    ap.add_argument("--interval", type=float, default=0.2, help="seconds between auto alerts")
    ap.add_argument("--wait", type=float, default=3.0, help="auto mode: wait for registrations")
    ap.add_argument("--level", type=int, choices=[1, 2, 3], default=0,
                    help="auto mode level (0 = random mix)")
    AlertServer(ap.parse_args()).run()
