import socket, threading, time, csv, os, json

PORT = 5000
TIMEOUT = 0.5
RETRIES = 3
TYPES = {"FIRE", "MEDICAL", "SECURITY", "GENERAL"}
LOG = "server_log.csv"
FEED = "alert_feed.jsonl"      # read by the dashboard's Alert feed panel
CLIENTS = "clients.json"       # read by the dashboard's Connected clients list
CTL = ("10.0.0.100", 9998)   # virtual controller IP (SDN event channel)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", PORT))
print(f"Server on port {PORT}")
print("Usage: TYPE PRIORITY message   e.g.  FIRE 1 fire in the building")
print("Types: FIRE MEDICAL SECURITY GENERAL | Priority: 1=high 2=medium 3=low")

def notify(kind, **extra):
    """Tell the SDN controller what the app is doing (ignored if no controller)."""
    try:
        sock.sendto(json.dumps({"type": kind, **extra}).encode(), CTL)
    except OSError:
        pass

if not os.path.exists(LOG):
    with open(LOG, "w", newline="") as f:
        csv.writer(f).writerow(["seq", "type", "priority", "client", "send_time",
                                "ack_time", "rtt_ms", "attempts", "status"])

def save_clients():
    """Write the registered client IPs for the dashboard."""
    try:
        with lock:
            names = sorted({c[0] for c in clients})
        with open(CLIENTS, "w") as f:
            json.dump(names, f)
    except OSError:
        pass

def log_feed(seq, atype, prio, msg):
    """Append one line per alert for the dashboard's Alert feed."""
    try:
        with open(FEED, "a") as f:
            f.write(json.dumps({"t": time.time(), "seq": seq, "type": atype,
                                "prio": int(prio), "msg": msg}) + "\n")
    except (OSError, ValueError):
        pass

seq = 0
clients = set()
acked = {}   # seq -> {addr: ack_time}
lock = threading.Lock()
open(FEED, "w").close()      # fresh feed and client list for each server run
save_clients()

def receiver():
    while True:
        data, addr = sock.recvfrom(1024)
        text = data.decode(errors="ignore")
        if text == "REGISTER":
            with lock:
                clients.add(addr)
            save_clients()
            notify("register", client=addr[0])
            print(f"\nregistered {addr}\nalert> ", end="", flush=True)
        elif text.startswith("BULK|"):
            try:
                _, n, gap = text.split("|")
                threading.Thread(target=bulk, args=(int(n), float(gap)),
                                 daemon=True).start()
            except ValueError:
                pass
        elif text.startswith("SEND|"):
            # single alert requested from outside (dashboard / sendalert.py): SEND|TYPE|PRIO|message
            try:
                _, t, pr, m = text.split("|", 3)
                if t.upper() in TYPES and pr in ("1", "2", "3") and m.strip():
                    threading.Thread(target=one_alert, args=(t.upper(), pr, m.strip()),
                                     daemon=True).start()
            except ValueError:
                pass
        elif text == "LEAVE":
            with lock:
                clients.discard(addr)
            save_clients()
            notify("unregister", client=addr[0])
            print(f"\nleft {addr}\nalert> ", end="", flush=True)
        elif text.startswith("ACK|"):
            try:
                s = int(text.split("|")[1])
            except ValueError:
                continue
            with lock:
                if s in acked and addr not in acked[s]:
                    acked[s][addr] = time.time()

def send_alert(seq, atype, prio, msg):
    log_feed(seq, atype, prio, msg)
    with lock:
        targets = set(clients)
        acked[seq] = {}
    if not targets:
        print("no clients registered")
        return
    t_send = time.time()
    packet = f"{seq}|{t_send}|{atype}|{prio}|{msg}".encode()
    pending = set(targets)
    attempts = {}
    for attempt in range(RETRIES + 1):
        for addr in pending:
            sock.sendto(packet, addr)
        if attempt > 0:
            print(f"retry {attempt} to {len(pending)} client(s)")
        deadline = time.time() + TIMEOUT
        while time.time() < deadline:
            with lock:
                still = targets - set(acked[seq])
            if not still:
                break
            time.sleep(0.01)
        with lock:
            still = targets - set(acked[seq])
        for addr in pending - still:
            attempts[addr] = attempt + 1
        pending = still
        if not pending:
            break

    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        for addr in targets:
            if addr in acked[seq]:
                t_ack = acked[seq][addr]
                w.writerow([seq, atype, prio, f"{addr[0]}:{addr[1]}", f"{t_send:.6f}",
                            f"{t_ack:.6f}", f"{(t_ack - t_send) * 1000:.3f}",
                            attempts[addr], "ACKED"])
            else:
                w.writerow([seq, atype, prio, f"{addr[0]}:{addr[1]}", f"{t_send:.6f}",
                            "", "", RETRIES + 1, "UNREACHABLE"])
    if pending:
        for addr in pending:
            print(f"UNREACHABLE: {addr}")
        with lock:
            pass  # keep clients so every alert is measured
    else:
        print(f"all ACKed ({len(targets)} clients)")

def one_alert(atype, prio, msg):
    global seq
    notify("alert_start")
    seq += 1
    send_alert(seq, atype, prio, msg)
    notify("alert_end")

def bulk(n, gap):
    global seq
    notify("alert_start")
    for i in range(n):
        seq += 1
        send_alert(seq, "FIRE", "1", f"test alert {i+1}")
        time.sleep(gap)
    notify("alert_end")
    print(f"\nbulk done: {n} alerts\nalert> ", end="", flush=True)

threading.Thread(target=receiver, daemon=True).start()

while True:
    line = input("alert> ").strip()
    if not line:
        continue
    parts = line.split(" ", 2)
    if len(parts) < 3 or parts[0].upper() not in TYPES or parts[1] not in ("1", "2", "3"):
        print("Bad format. Use: TYPE PRIORITY message")
        continue
    seq += 1
    notify("alert_start")
    send_alert(seq, parts[0].upper(), parts[1], parts[2])
    notify("alert_end")
