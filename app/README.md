# AlertApp: send messages between machines, with a live traffic monitor

One Python file. Needs only Python 3 (Windows, Linux, macOS). No installs.

## Run it on two machines
Machine 1:  `python3 alertapp.py --name server`
Machine 2:  `python3 alertapp.py --name client1`
Open the UI on each machine: http://localhost:8080
On machine 2, in "Peers", type machine 1's IP address and port 5000, press Join.
Both machines now list each other. Use "Send a message" to send from either side.

Allow the ports in the firewall:
- Ubuntu: `sudo ufw allow 5000:5002/udp`
- Windows: allow Python through Windows Defender Firewall when it asks
- VirtualBox VMs: set the network adapter to **Bridged**, so the VMs can reach each other

Testing on one machine: run `--port 5000 --ui-port 8080` and `--port 6000 --ui-port 8081`, then join 127.0.0.1:5000 from the second one.

## Traffic classes (shown separately in the monitor)
| Class | UDP port | Reliability |
|---|---|---|
| EMERGENCY | N (5000) | ACK + up to 4 retries |
| NORMAL | N+2 (5002) | ACK + up to 3 retries |
| BULK (background generator) | N+2 (5002) | none |

## Queue policy (buttons "static / priority / dynamic")
- static: one FIFO queue, all classes wait in the same line
- priority: EMERGENCY is always sent first
- dynamic: priority, and BULK is dropped once the queue builds up

## Demo of the effect
1. Join the two machines.
2. Set the emulated uplink to 500 kbit/s (box 1, "Set").
3. Start the generator at 200 packets/s (box 4). The queue counters grow and the chart shows the load.
4. Send EMERGENCY messages in each mode and compare the "avg RTT" column.
   Test on loopback: static about 5400 ms with failures, priority about 9 ms, dynamic about 9 ms.

## Notes
- RTT is measured at the sender (enqueue to ACK), so it needs no clock sync between machines.
- The uplink speed limit is emulated by the app, because a real LAN rarely congests by itself.
- The UI listens on localhost only. `--ui-host 0.0.0.0` opens it to your network (there is no password).
- Messages are plain UDP, not encrypted.
- Optional: `--ctl-ip 10.0.0.100` sends alert_start / alert_end events to the SDN controller (port 9998), like server.py does.
