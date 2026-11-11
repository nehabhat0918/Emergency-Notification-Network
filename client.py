import socket, sys, time, csv, os

if len(sys.argv) < 2:
    print("Usage: python3 client.py <server_ip>")
    sys.exit(1)

server_ip = sys.argv[1]
PORT = 5000

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(b"REGISTER", (server_ip, PORT))
logname = f"client_log_{os.getpid()}.csv"
logf = open(logname, "w", newline="")
log = csv.writer(logf)
log.writerow(["seq", "type", "priority", "send_ts", "recv_ts", "latency_ms"])
logf.flush()
print(f"Registered, logging to {logname} (Ctrl+C to leave)")

seen = set()
try:
    while True:
        data, addr = sock.recvfrom(1024)
        now = time.time()
        try:
            seq, ts, atype, prio, msg = data.decode().split("|", 4)
            latency = (now - float(ts)) * 1000
        except ValueError:
            print("ignored malformed packet")
            continue
        sock.sendto(f"ACK|{seq}".encode(), addr)
        if seq in seen:
            continue
        seen.add(seq)
        log.writerow([seq, atype, prio, ts, f"{now:.6f}", f"{latency:.3f}"])
        logf.flush()
        print(f"[{atype} P{prio}] ALERT #{seq}: {msg} ({latency:.2f} ms)")
except KeyboardInterrupt:
    sock.sendto(b"LEAVE", (server_ip, PORT))
    logf.close()
    print("\nLeft the network")
