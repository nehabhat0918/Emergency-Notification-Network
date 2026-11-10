# Run Guide (Ubuntu)

Commands to set up and run the Emergency Notification Network project (UDP alerts + Ryu SDN controller + Mininet).
Use Ubuntu with a desktop, because the demo opens xterm windows.

---

## 1. One-time setup (about 10 minutes)

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf xterm python3-pip git unzip curl
sudo systemctl enable --now openvswitch-switch
pip3 install --break-system-packages matplotlib 2>/dev/null || pip3 install matplotlib
```

Ryu needs an older Python than Ubuntu ships, so use a Python 3.9 environment:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uv venv --python 3.9 ~/ryu-env
source ~/ryu-env/bin/activate
uv pip install "setuptools==58.2.0" wheel
uv pip install --no-build-isolation ryu "eventlet==0.30.2"
deactivate
```

Check that Mininet works (it should end with `0% dropped`):

```bash
sudo mn --test pingall
```

---

## 2. Get the project files (pick one way)

### A. Without cloning (from the zip)

```bash
cd ~/Downloads
unzip Emergency-Notification-Network_complete.zip -d ~
cd ~/Emergency-Notification-Network
```

### B. By cloning from GitHub

```bash
cd ~
git clone https://github.com/nehabhat0918/Emergency-Notification-Network
cd Emergency-Notification-Network
```

Already cloned? Update it instead:

```bash
cd ~/Emergency-Notification-Network
git pull
```

---

## 3. Run the demo (every time)

Always run from inside the project folder.

```bash
cd ~/Emergency-Notification-Network
bash run_all.sh
```

This asks for your sudo password, then starts the Ryu controller, the dashboard, the Mininet network, the server (h1) and two clients (h2, h3). Windows open for the server and clients, and you end up at a `mininet>` prompt.

1. Open the dashboard: `http://localhost:8000`
2. In the **SERVER h1** window, send an alert:
   ```
   FIRE 1 fire in the building
   ```
   Both client windows print it with its latency, and the server says `all ACKed`.
3. Create congestion. At the `mininet>` prompt:
   ```
   h6 iperf -s -u &
   h5 iperf -u -c 10.0.0.6 -b 20M -t 60 &
   ```
   (or use the traffic buttons on the dashboard)
4. Compare the modes: for each of **static**, **priority**, **dynamic**, select the mode on the dashboard, keep the same traffic running, and press **Send alerts** (for example 100).
5. Link failure test: stop the traffic, select **dynamic**, press **link s1-s3 down**, send alerts, then press **link up**. Repeat in **static** to see the difference.
6. Stop everything: type `exit` at the `mininet>` prompt. The script cleans up.

Start in a specific mode:

```bash
MODE=static bash run_all.sh      # or priority, or dynamic (default)
```

---

## 4. Results for the report

```bash
bash run_all.sh quick      # about 5 minutes, results in results/quick/
bash run_all.sh full       # 3 repetitions, 30+ minutes, results in results/full/
```

Each folder holds a summary table (`summary.md`, `summary.csv`) and graphs. Commit the `results/` folder to GitHub and copy the main table into the README.

Check one run in the terminal:

```bash
python3 analyze.py
```

---

## 5. Manual run (step by step, no run_all.sh)

Terminal 1, controller:

```bash
sudo mn -c
source ~/ryu-env/bin/activate
SDN_MODE=dynamic ryu-manager --ofp-tcp-listen-port 6653 controller/emergency_controller.py
```

Terminal 2, network:

```bash
sudo python3 topo/b_test_topo.py
mininet> xterm h1 h2 h3
```

In the xterm windows:

```bash
# h1
python3 server.py
# h2 and h3
python3 client.py 10.0.0.1
```

Terminal 3, dashboard:

```bash
sudo python3 dashboard/app.py
```

Change the controller mode and send bulk alerts from the `mininet>` prompt:

```bash
mininet> h1 python3 src/ctl_send.py mode static      # or priority, or dynamic
mininet> h1 python3 bulk.py 100 0.05
mininet> h1 python3 analyze.py
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1    # show the flow rules
mininet> link s1 s3 down                              # break the direct link
mininet> link s1 s3 up                                # repair it
```

More detail is in `DEMO.md`.

---

## 6. Two laptops (alert app only, no Mininet)

On both laptops, on the same Wi-Fi or hotspot:

```bash
sudo ufw allow 5000/udp
```

Laptop 1 (server), find its IP with `hostname -I`, then:

```bash
python3 alert_ui.py server
# open http://localhost:8080
```

Laptop 2 (client), replacing `192.168.1.23` with laptop 1's IP:

```bash
python3 alert_ui.py client 192.168.1.23
# open http://localhost:8081
```

Terminal-only version (no web page):

```bash
# laptop 1
python3 server.py
# laptop 2
python3 client.py 192.168.1.23
```

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `Ryu environment not found` | Redo the Ryu setup in section 1, or run `RYUENV=/path/to/env bash run_all.sh` |
| Mininet complains about leftover state | `sudo mn -c`, then run again |
| Ryu errors on start | `tail -n 30 results/ryu.log` |
| Clients print nothing | Make sure the server window is open and run `pingall` at `mininet>` |
| Dashboard shows old numbers | Stop the server, then `rm server_log.csv client_log_*.csv`, and restart |
| `mn -c` kills Ryu | Run `sudo mn -c` only when Ryu is stopped |
| Two laptops cannot connect | Use a phone hotspot, `ping` the server IP, and allow UDP 5000 in the firewall |
