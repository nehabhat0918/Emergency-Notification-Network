# Part B: the SDN controller

## What this part does
The Ryu controller is the brain of the network. It tells the switches where to send packets, gives alert traffic priority, reacts when the server announces an alert, moves normal traffic out of the way when the link is crowded, and keeps alerts flowing if the direct link fails.

## Agreed values
| Item | Value |
|---|---|
| Server h1 | 10.0.0.1, UDP port 5000 |
| Clients h2, h3, h4 | 10.0.0.2 to 10.0.0.4 |
| Background traffic h5 to h6 | 10.0.0.5 to 10.0.0.6, iperf port 5001 |
| App events to the controller | JSON over UDP port 9998 to the virtual IP 10.0.0.100 |

## My files
| File | Purpose |
|---|---|
| `src/network_config.py` | Constants: IPs, ports, which switch port each host and link uses, queue rates, thresholds |
| `controller/emergency_controller.py` | The Ryu controller |
| `src/ctl_send.py` | Sends test events and mode changes to the controller |
| `topo/b_test_topo.py` | Mininet topology: 3 switches, 6 hosts, 10 Mbps links with two queues |
| `dashboard/app.py` | Web console to send alerts, generate traffic and watch the network |
| `tests/test_failover.py` | Checks the failover rules without needing Mininet |

## How the controller works
1. **Routing (priority 10).** Every switch forwards by destination IP. This carries all normal traffic.
2. **Emergency rule (priority 100).** A UDP packet whose source or destination port is 5000 is emergency traffic. It goes into queue 1, the high-priority queue on the switch-to-switch links. iperf uses port 5001, so it stays in the normal queue. OpenFlow cannot say "source or destination", so there is one rule for each.
3. **Event channel (priority 200, on s1).** UDP to 10.0.0.100 port 9998 is sent to the controller as a packet-in. This is how the application talks to the network.
4. **Alert start.** When the server starts a high-priority alert, the controller moves normal traffic off the direct link s1-s3 onto s1-s2-s3, so alerts get a clear path before congestion builds.
5. **Congestion monitor.** Every 2 seconds the controller reads the byte counter on the s1-to-s3 port. At 7 Mbps or more it does the same reroute for 15 seconds, then traffic returns to the direct path.
6. **Link-failure failover.** If the direct link goes down, the controller moves all traffic, emergency included, onto s1-s2-s3, and moves it back when the link returns. Emergency packets keep queue 1 on the alternate links.

## The three modes
| Mode | What it does |
|---|---|
| static | Baseline: routing only, no emergency rule, no reaction to anything |
| priority | Routing plus the emergency rule and queue 1 |
| dynamic | Priority, plus rerouting on congestion or alert start, plus failover on link failure |

Change the mode with `python3 src/ctl_send.py mode static` (or `priority`, `dynamic`).

## Switch ports the controller relies on
- s1: h1 on port 1, h5 on port 2, link to s3 on port 3, link to s2 on port 4.
- s2: link to s1 on port 1, link to s3 on port 2.
- s3: h2, h3, h4 and h6 on ports 1 to 4, link to s1 on port 5, link to s2 on port 6.

The direct path uses s1 port 3 and s3 port 5. A different topology file must use the same numbers.

## How to run and check it
```bash
# terminal 1
source ~/ryu-env/bin/activate
SDN_MODE=dynamic ryu-manager --ofp-tcp-listen-port 6653 controller/emergency_controller.py

# terminal 2
sudo mn -c
sudo python3 topo/b_test_topo.py
```
The controller log should show all three switches connecting, and `pingall` should report 0% dropped. Useful checks at the `mininet>` prompt:
```text
sh ovs-ofctl -O OpenFlow13 dump-flows s1
h1 python3 src/ctl_send.py alert_start
h1 python3 src/ctl_send.py mode static
link s1 s3 down
link s1 s3 up
```
- `dump-flows s1` shows the priority 200, 100 and 10 rules, and the priority 100 rules use `set_queue:1`.
- After `alert_start` the log says `REROUTE normal traffic OFF the direct link`, and about 15 seconds later `back ONTO`.
- After `link s1 s3 down` in dynamic mode the log says `LINK s1-s3 DOWN -> FAILOVER`, and `link s1 s3 up` brings it back.

## Testing and limits
- The controller ran on real Ryu and Mininet in the first integration run. The log shows the switches connecting, mode changes, app events, reroutes and the hold timer working.
- Failover was tested with stand-in Ryu classes (`python3 tests/test_failover.py`). It still needs a run on real Mininet.
- If a flood lasts longer than the 15 s hold, traffic moves back and the link fills again, so the reroute repeats about every 17 s. The hold time prevents rapid flapping.
- Failover covers only the direct link s1-s3, and only in dynamic mode. In static and priority mode a failed link loses alerts, which is the baseline for the comparison.

## Questions to be ready for
- **How does the controller recognise emergency traffic?** UDP source or destination port 5000.
- **Why two rules for it?** OpenFlow has no "or" in a match.
- **What is a packet-in, and why use it?** The switch sends a packet to the controller, so the application can talk to the network without an extra connection.
- **Why is the event rule only on s1?** The server is on s1.
- **What does queue 1 do?** It gives alerts a guaranteed rate and priority on a crowded link.
- **Why a hold time?** Without it the controller would flip traffic back and forth.
- **What if the controller stops?** Installed rules stay, new reroutes stop, and the application keeps working.
- **Why doesn't iperf get priority?** It uses port 5001, not 5000.
- **What happens when the direct link fails?** In dynamic mode all traffic moves to s1-s2-s3 within moments; in static and priority mode alerts are lost.
