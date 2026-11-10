# Demo cheat sheet (run everything from the project root)

Folder layout expected:
  controller/emergency_controller.py   src/network_config.py  src/ctl_send.py
  topo/b_test_topo.py   server.py client.py bulk.py analyze.py   dashboard/app.py

## 0. One-time
  sudo apt install -y mininet openvswitch-switch xterm iperf
  pip install ryu            # (or your existing Ryu install)

## 1. Clean, then terminal 1 - controller
  sudo mn -c
  SDN_MODE=dynamic ryu-manager controller/emergency_controller.py

## 2. Terminal 2 - network
  sudo python3 topo/b_test_topo.py        # prints pingall, then mininet> prompt
  mininet> xterm h1 h2 h3

## 3. In the xterms
  h1:  python3 server.py
  h2:  python3 client.py 10.0.0.1
  h3:  python3 client.py 10.0.0.1

## 4. Terminal 3 - dashboard (optional, nice for the teacher)
  sudo python3 dashboard/app.py           # open http://localhost:8000

## 5. The story to show
 a) h1 xterm:  FIRE 1 fire in building        -> both clients print it with ms latency
 b) mininet>   h6 iperf -s -u &
               h5 iperf -u -c 10.0.0.6 -b 20M -t 40 &      (congests the 10 Mbps link)
 c) mininet>   h1 python3 src/ctl_send.py mode static
               h1 python3 bulk.py 100 0.05     -> note loss/latency  (python3 analyze.py)
 d) rm server_log.csv client_log_*.csv, restart server+clients, repeat with:
               h1 python3 src/ctl_send.py mode priority     -> lower latency
               h1 python3 src/ctl_send.py mode dynamic      -> reroutes iperf via s2
 e) mininet>   sh ovs-ofctl -O OpenFlow13 dump-flows s1     (show the emergency rule)
 f) mininet>   link s1 s3 down   (dynamic mode fails over to s1-s2-s3; static/priority lose alerts)
               send alerts, then:  mininet> link s1 s3 up

Compare the analyze.py output of the three modes in a small table.
