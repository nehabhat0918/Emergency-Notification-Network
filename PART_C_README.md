# Changes for Part C

Copy these files over the same paths in the project root, replacing the old ones.

| File in this zip | Replace this file | What changed |
|---|---|---|
| experiments/run_experiments.py | experiments/run_experiments.py | Link-failure scenario now overlaps the alerts. New loss scenario. New column loss % during outage. New --only option |
| analyze.py | analyze.py | Optional folder argument. No crash on an empty log |
| c_experiments.sh | c_experiments.sh | Updated sanity-check text |
| run_all.sh | run_all.sh | Finds ~/ryu-env automatically, starts Ryu with port 6653 |
| c_setup.sh, c_run.sh | Same paths | Unchanged |

Needs the updated controller from Part B:
controller/emergency_controller.py

## Run

Terminal 1:

source ~/ryu-env/bin/activate

ryu-manager --ofp-tcp-listen-port 6653 controller/emergency_controller.py

Terminal 2:

sudo python3 experiments/run_experiments.py --only linkfail

Then:

bash c_experiments.sh

## What to expect

| Scenario | static | priority | dynamic |
|---|---|---|---|
| congestion | Highest latency | Lower | Lower |
| linkfail | Loss during outage | Loss during outage | About 0, recovers |
| loss | About 10% first-try loss | Same | Same |

The loss scenario demonstrates ACK and retransmission.
