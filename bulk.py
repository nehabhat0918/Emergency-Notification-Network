import socket, time, sys

N = int(sys.argv[1]) if len(sys.argv) > 1 else 100
GAP = float(sys.argv[2]) if len(sys.argv) > 2 else 0.05
PORT = 5000

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(b"BULK|" + str(N).encode() + b"|" + str(GAP).encode(), ("127.0.0.1", PORT))
print(f"Asked server to send {N} alerts, {GAP}s apart")
