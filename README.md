# Emergency Notification Network (UDP + SDN)

CN mini project #16: a UDP-based emergency notification system. A server sends urgent alerts to many registered
clients. An SDN controller (Ryu, OpenFlow 1.3) recognises emergency traffic, gives it priority, and reroutes other
traffic when the link is crowded. Everything runs in Mininet. A second, standalone app (`app/`) sends messages
between real machines with a live traffic monitor.

## Network
```
 h1 server, h5 iperf source            h2, h3, h4 clients, h6 iperf sink
            \                                   /
             [s1] ==== direct link, 10 Mbps ==== [s3]
                \                               /
                 +-------- [s2] alternate ------+
```
| Item | Value |
|---|---|
| Server h1 | 10.0.0.1, UDP port 5000 |
| Clients h2, h3, h4 | 10.0.0.2 to 10.0.0.4 |
| Background traffic h5 to h6 | 10.0.0.5 to 10.0.0.6, iperf port 5001 |
| App events to controller | UDP 9998 to virtual IP 10.0.0.100 |

## Controller modes (`controller/emergency_controller.py`)
| Mode | Behaviour |
|---|---|
| static | baseline: routing only, one queue |
| priority | UDP port 5000 traffic goes into the high-priority queue (queue 1) |
| dynamic | priority, plus normal traffic is moved to s1-s2-s3 when an alert starts or the s1-s3 port reaches 7 Mbps (held 15 s) |

Flow priorities: 200 event channel (to controller), 100 emergency traffic, 10 normal routing.
Link failure: in dynamic mode the controller watches the s1 port for the direct link. When s1-s3 goes down it moves ALL traffic (alerts included) to s1-s2-s3, and moves it back when the link returns. Static and priority do not react (they are the baseline). Logic test: `python3 tests/test_failover.py`.

## Layout
```
server.py client.py bulk.py analyze.py   alert application (UDP, ACK + retransmit, CSV logs)
controller/emergency_controller.py       Ryu controller
src/network_config.py src/ctl_send.py    shared constants; tool to send events / change mode
topo/b_test_topo.py                      Mininet topology (3 switches, 6 hosts, 10 Mbps queues, static ARP)
experiments/run_experiments.py           automatic experiments: modes x scenarios, tables and graphs
dashboard/app.py                         live dashboard (http://localhost:8000)
run_all.sh demo_launcher.py DEMO.md      one-command demo and the demo script
app/alertapp.py                          standalone cross-machine messaging app with traffic monitor UI
alert_ui.py                              optional web page version of server.py/client.py (same protocol and logs)
sendalert.py                             sends one alert through the running server (dashboard send button)
alert_server.py alert_client.py analyze_logs.py README_partA.md   Part A's alternative JSON version of the alert app (not used by the scripts)
docs/                                    notes
results/d1_evidence/                     Ryu log and latency logs from the D1 run
```

## Setup (Ubuntu with Mininet)
```bash
sudo apt install -y mininet openvswitch-switch iperf xterm python3-pip
```
Ryu needs an older Python than recent Ubuntu ships. Use a Python 3.9 environment, for example:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.9 ~/ryu-env
source ~/ryu-env/bin/activate
uv pip install "setuptools==58.2.0" wheel
uv pip install --no-build-isolation ryu "eventlet==0.30.2"
```

## Run
Everything from the project root. Run `sudo mn -c` only when Ryu is stopped (it kills ryu-manager).

One command (the script looks for the Ryu environment in `~/ryuenv` or `~/ryu-env`, or set `RYUENV=...`):
```bash
bash run_all.sh            # demo: controller, network, server, clients, dashboard
bash run_all.sh quick      # automatic experiments, about 5 minutes
bash run_all.sh full       # 3 repetitions
```
Manual, step by step: see `DEMO.md`.

Quick manual test:
```bash
# terminal 1
source ~/ryu-env/bin/activate
ryu-manager --ofp-tcp-listen-port 6653 controller/emergency_controller.py
# terminal 2
sudo python3 topo/b_test_topo.py
mininet> pingall
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1
mininet> h1 python3 src/ctl_send.py alert_start
```

## Standalone app (no Mininet needed)
```bash
python3 app/alertapp.py --name laptop1      # UI at http://localhost:8080
```
Run it on two machines and join one from the other. See `app/README.md`.

## Results
`results/d1_evidence/ryu_d1_run.log` shows the controller connecting, mode changes, app events and reroutes on a real run.
Experiment output goes to `results/quick/` and `results/full/` (tables and graphs).
