# Part A: UDP Emergency Alert Application

Files (put in `src/`):
- `alert_server.py` : UDP server. Registration, fan-out delivery, ACK + retransmit, levels, CSV log
- `alert_client.py` : UDP subscriber. REGISTER, ACK every alert, dedupe, CSV log
- `analyze_logs.py` : latency / loss / retransmission statistics from the CSV logs

## Protocol (JSON over UDP, port 5000)
| type | direction | fields |
|---|---|---|
| REGISTER / UNREGISTER | client -> server (dst port 5000) | - |
| REGISTERED | server -> client | timestamp |
| ALERT | server (src port 5000) -> client | priority (1 critical, 2 warning, 3 info), seq_no, timestamp, text, retry |
| ACK | client -> server (dst port 5000) | seq_no, client_ts |

The controller can classify emergency traffic by UDP port 5000 (dst port for REGISTER/ACK,
src port for ALERT). With `--dscp` the server also sets IP TOS by priority (EF/AF31/best effort).

## Reliability
Each ALERT must be ACKed per client. If no ACK within `--timeout` (default 0.5 s) the server
resends, up to `--retries` (default 3). A client failing `--max-fail` alerts in a row (default 3)
is removed. Clients ACK duplicates but only log/print them once. `timestamp` is the first-send
time, so measured latency includes retransmission delay.

## Run (Mininet: h1 server, h2..h4 clients)
```
h1: python3 src/alert_server.py                      # interactive: "2 storm warning", list, quit
h2: python3 src/alert_client.py 10.0.0.1 --log client_h2.csv
h3: python3 src/alert_client.py 10.0.0.1 --log client_h3.csv
```
Automated experiment (for Part C):
```
h1: python3 src/alert_server.py --auto 200 --interval 0.05 --wait 5 --log results/run1_server.csv
```
Loss test (clients only): add `--drop-prob 0.3` to a client.
Results: `python3 src/analyze_logs.py --server results/run1_server.csv --clients client_h2.csv client_h3.csv`

## Server options
`--port --timeout --retries --max-fail --log --dscp --auto N --interval S --wait S --level 1|2|3`
