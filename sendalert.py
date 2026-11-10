"""Send ONE alert through the running server (used by the dashboard's send button).

    python3 sendalert.py FIRE 1 fire in the building
"""
import socket, sys

if len(sys.argv) < 4:
    print("Usage: python3 sendalert.py TYPE PRIORITY message")
    sys.exit(1)
atype, prio, msg = sys.argv[1], sys.argv[2], " ".join(sys.argv[3:]).replace("|", "/")
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(f"SEND|{atype}|{prio}|{msg}".encode(), ("127.0.0.1", 5000))   # server runs on the same host (h1)
print(f"asked the server to send: {atype} P{prio} {msg}")
