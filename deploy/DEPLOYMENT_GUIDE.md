# Step-by-Step Deployment & Testing Guide

## VM IPs (from `deploy/env.sh`)

| VM | Role | IP |
|----|------|----|
| VM1 | PC1 — Sensors + Broker | `10.43.98.238` |
| VM2 | PC2 — Analytics + Control + Replica DB | `10.43.100.96` |
| VM3 | PC3 — Primary DB + Monitoring CLI | `10.43.99.5` |

---

## Phase 1: Setup (one-time, on each VM)

Open a terminal on **each** of the 3 VMs (via RDP or SSH) and run:

```bash
# 1. Clone the repo
git clone https://github.com/Mundolel/Distribuidos.git ~/traffic-system/app

# 2. Switch to the deployment branch
cd ~/traffic-system/app
git checkout deploy-bare-metal-vms

# 3. Run the setup script (installs Python, creates venv, installs dependencies)
bash deploy/setup_vm.sh
```

### What `setup_vm.sh` does

- If you have sudo: installs Python 3.11 from deadsnakes PPA + git
- If no sudo: uses the system Python 3.10 (pre-installed on Ubuntu 22.04)
- Creates `~/traffic-system/venv` (virtualenv) and `~/traffic-system/data` (for SQLite DBs)
- Runs `pip install -r requirements.txt` (installs pyzmq, pytest, matplotlib)

### How to verify it worked

At the end you should see:

```
=== Setup complete ===
Project dir : /home/<user>/traffic-system/app
Virtualenv  : /home/<user>/traffic-system/venv
Python      : Python 3.11.x   (or 3.10.x)
pyzmq       : 25.x.x
```

If `setup_vm.sh` fails because the repo was already cloned in step 1, that's fine — it detects the existing repo and skips cloning. The venv/pip part still runs.

---

## Phase 2: Firewall (optional, one-time, requires sudo)

Only needed if the VMs have UFW active. On **each** VM:

```bash
cd ~/traffic-system/app
sudo bash deploy/firewall_open.sh
```

The script auto-detects which VM it's running on by matching the local IP against env.sh, and opens only the needed ports:

- VM1: port 5560
- VM2: port 5561
- VM3: ports 5563, 5565

If you don't have sudo or UFW isn't active, skip this — Ubuntu 22.04 has no firewall enabled by default.

---

## Phase 3: Launch the System

**The order matters.** Open a terminal on each VM and run these in sequence:

### Step 1 — Start VM2 first (Analytics)

```bash
cd ~/traffic-system/app
bash deploy/start_vm2.sh
```

You should see:

```
=== Starting PC2: Analytics, Control & Replica DB ===
VM2 IP        : 10.43.100.96
Replica DB    : /home/<user>/traffic-system/data/traffic_replica.db
```

Then logs from db_replica, traffic_light_control, and analytics_service starting up. **Wait ~3 seconds** for all sockets to bind.

### Step 2 — Start VM3 second (DB + Monitoring)

```bash
cd ~/traffic-system/app
bash deploy/start_vm3.sh
```

You should see:

```
=== Starting PC3: Primary DB & Monitoring ===
VM3 IP      : 10.43.99.5
Primary DB  : /home/<user>/traffic-system/data/traffic_primary.db
```

Then the interactive monitoring menu appears. **Wait ~3 seconds.**

### Step 3 — Start VM1 last (Sensors)

```bash
cd ~/traffic-system/app
bash deploy/start_vm1.sh
```

You should see:

```
=== Starting PC1: Sensors & Broker ===
VM1 IP     : 10.43.98.238
Broker mode: standard
```

Then sensor publishing messages start flowing.

---

## Phase 4: Verify the System Works

All verification is done on **VM3** (the monitoring CLI). It should be showing the menu:

```
1. Consultar estado de interseccion
2. Consultar historial de congestion
3. Forzar ola verde
4. Forzar cambio de semaforo
5. Estado del sistema
6. Health check
0. Salir
```

### Test 1 — Health Check (PC3 to PC2 connectivity)

```
Type: 6
```

**Expected:** `Analytics Service: OK` or similar PONG response. This proves PC3 can reach PC2 via ZMQ REQ/REP on port 5561.

**If it fails:** PC3 can't reach PC2. Check that VM2 is running and port 5561 is accessible (`ping 10.43.100.96` from VM3).

### Test 2 — System Status (full pipeline)

```
Type: 5
```

**Expected:** A summary showing `total_sensor_events` > 0 and increasing. Also shows congestion counts and semaphore changes. This proves the full data pipeline works: VM1 sensors -> VM1 broker -> VM2 analytics -> VM3 primary DB.

**If `total_sensor_events` is 0:** Data isn't flowing from VM1. Check that VM1 is running and VM2 logs show `[SUB] Connected to tcp://10.43.98.238:5560`.

### Test 3 — Query an Intersection

```
Type: 1
Enter intersection: INT-A1
```

**Expected:** Shows current Q (queue), Vp (average speed), D (density) values and a list of recent sensor events. This proves analytics is processing sensor data and storing it.

### Test 4 — Force a Green Wave (user command)

```
Type: 3
Enter row or column (e.g. A or 1): A
Enter reason: ambulance
```

**Expected:** Confirmation that all intersections on row A (INT-A1 through INT-A4) are set to GREEN for 30 seconds. Check VM2 logs for `[GREEN_WAVE]` messages and traffic light state changes.

---

## Phase 5: Test Failover (Kill PC3)

This is the most important distributed systems test — proving the system survives a node failure.

### Step 1 — Kill VM3

On the VM3 terminal, press **Ctrl+C**. This kills both the primary DB and monitoring service.

### Step 2 — Watch VM2 logs

On VM2's terminal, within ~15 seconds (3 health check retries x 5s interval), you should see:

```
[FAILOVER] PC3 is down. Using replica DB on PC2.
[FAILOVER] Disconnecting primary DB PUSH socket
```

Data continues flowing — VM1 sensors still publish, VM2 analytics still processes, but now only writes to the local replica DB.

### Step 3 — Use fallback monitoring on VM2

Open a **second terminal** on VM2:

```bash
cd ~/traffic-system/app
bash deploy/fallback_monitor.sh
```

This gives you the same monitoring menu but querying the replica DB. Type `5` for system status — it should show `PC3 Status: DOWN (FAILOVER)` and `total_sensor_events` still increasing.

### Step 4 — Recover VM3

On VM3, restart:

```bash
bash deploy/start_vm3.sh
```

Watch VM2 logs for:

```
[RECOVERY] PC3 is back. Resuming writes to primary DB.
[RECOVERY] Reconnecting primary DB PUSH socket
```

On VM3's monitoring CLI, type `5` — the system should show as healthy again.

---

## Phase 6: Performance Experiments

These compare the **standard broker** vs **threaded broker** under two load levels (Table 1 from the project spec).

For each scenario, you only change VM1. VM2 and VM3 stay running.

### Scenario 1A — 1 sensor/type, 10s interval, standard broker

```bash
# On VM1:
BROKER_MODE=standard SENSOR_COUNT=1 SENSOR_INTERVAL=10 bash deploy/start_vm1.sh
```

Wait 2 minutes, then Ctrl+C on VM1.

### Scenario 1B — 1 sensor/type, 10s interval, threaded broker

```bash
BROKER_MODE=threaded SENSOR_COUNT=1 SENSOR_INTERVAL=10 bash deploy/start_vm1.sh
```

Wait 2 minutes, then Ctrl+C.

### Scenario 2A — 2 sensors/type, 5s interval, standard broker

```bash
BROKER_MODE=standard SENSOR_COUNT=2 SENSOR_INTERVAL=5 bash deploy/start_vm1.sh
```

Wait 2 minutes, then Ctrl+C.

### Scenario 2B — 2 sensors/type, 5s interval, threaded broker

```bash
BROKER_MODE=threaded SENSOR_COUNT=2 SENSOR_INTERVAL=5 bash deploy/start_vm1.sh
```

Wait 2 minutes, then Ctrl+C.

### Collecting results

**Throughput** (run on VM2 after each scenario):

```bash
source ~/traffic-system/venv/bin/activate
export PYTHONPATH=~/traffic-system/app
cd ~/traffic-system/app
python -c "
from common.db_utils import TrafficDB
db = TrafficDB('$HOME/traffic-system/data/traffic_replica.db')
print('Total events:', db.get_event_count_in_interval('2020-01-01T00:00:00Z', '2030-01-01T00:00:00Z'))
"
```

**Latency** — look for `[LATENCY]` lines in VM2's terminal output. Tip: redirect VM2 output to a log file:

```bash
bash deploy/start_vm2.sh 2>&1 | tee ~/traffic-system/data/vm2.log
```

Then after experiments:

```bash
grep LATENCY ~/traffic-system/data/vm2.log
```

**Reset between scenarios** — delete the DBs to start fresh:

```bash
# On VM2:
rm ~/traffic-system/data/traffic_replica.db
# On VM3:
rm ~/traffic-system/data/traffic_primary.db
```

Then restart VM2 and VM3.

---

## Connectivity Troubleshooting

If things aren't working, run the connectivity test from any VM:

```bash
cd ~/traffic-system/app
bash deploy/test_connectivity.sh
```

This checks:

- Ping to all 3 VMs
- TCP port reachability (5560, 5561, 5563, 5565) — only works after services are running

### Common issues

- **Ping fails:** VMs aren't on the same network, or wrong IPs in `deploy/env.sh`
- **Ports fail:** Services not started yet, or firewall blocking. Try `sudo ufw status` on the target VM
- **VM2 can't reach VM1:5560:** Start VM1 first, or check that the broker is running

---

## Viewing the Database (DB Browser for SQLite)

Install on **VM2** and/or **VM3**:

```bash
sudo apt-get install -y sqlitebrowser
```

Open the databases:

```bash
# On VM3 (primary DB):
sqlitebrowser ~/traffic-system/data/traffic_primary.db

# On VM2 (replica DB):
sqlitebrowser ~/traffic-system/data/traffic_replica.db
```

### Tables to inspect

| Table | Contents |
|-------|----------|
| `sensor_events` | Raw sensor readings (camera, inductive, GPS) |
| `congestion_history` | Analytics decisions (NORMAL / CONGESTION / GREEN_WAVE) |
| `semaphore_states` | Traffic light state changes (NS/EW direction, reason) |
| `priority_actions` | Green waves and forced semaphore changes |

During normal operation both DBs have identical data. During failover (PC3 down), only VM2's replica keeps receiving inserts. If you only want to install it on one VM, pick **VM2** — it always has data.

---

## Tracing a Single Event Across All 3 VMs

This section helps you see that a single sensor event travels through the entire distributed pipeline: VM1 (generated) -> VM1 (broker forwarded) -> VM2 (analytics processed) -> VM2 (replica DB inserted) -> VM3 (primary DB inserted).

### Recommended setup: minimal sensors + log files

Start the system with only **1 sensor per type** so the output is clean and readable:

```bash
# VM2 (first) — save logs to file AND show on screen:
cd ~/traffic-system/app
bash deploy/start_vm2.sh 2>&1 | tee ~/traffic-system/data/vm2.log

# VM3 (second) — save logs:
cd ~/traffic-system/app
bash deploy/start_vm3.sh 2>&1 | tee ~/traffic-system/data/vm3.log

# VM1 (last) — 1 sensor per type, 10s interval, save logs:
cd ~/traffic-system/app
SENSOR_COUNT=1 SENSOR_INTERVAL=10 bash deploy/start_vm1.sh 2>&1 | tee ~/traffic-system/data/vm1.log
```

With `SENSOR_COUNT=1`, only 3 sensors run (CAM-A1, ESP-A2, GPS-A1), producing one event every ~3.3 seconds. Much easier to follow.

### What each VM logs for one event

The data flow for a camera event on intersection INT-A1 looks like this:

**VM1 — Sensor generates and publishes:**
```
[CAM-A1 @ INT-A1] volumen=12, velocidad=25.3 km/h
```

**VM1 — Broker forwards to PC2:**
```
[FORWARD #7] topic=camara | size=198 bytes
```

**VM2 — Analytics receives, evaluates rules, makes decision:**
```
[EVENT #7] CAM-A1 @ INT-A1 -> state=NORMAL, decision=NO_ACTION (Q=12, Vp=25.3, D=5)
```

**VM2 — If congestion detected, semaphore control applies change:**
```
[INT-A1] NS: RED->GREEN, EW: GREEN->RED (reason: congestion detected, cycle: 25s)
```

**VM2 — Replica DB inserts the record:**
```
[INSERT sensor_event] CAM-A1 @ INT-A1 (CAMARA)
[INSERT congestion_record] INT-A1 state=NORMAL decision=NO_ACTION
```

**VM3 — Primary DB inserts the same record:**
```
[INSERT sensor_event] CAM-A1 @ INT-A1 (CAMARA)
[INSERT congestion_record] INT-A1 state=NORMAL decision=NO_ACTION
```

### Grep commands to filter one trace

After running for a while, use these commands to extract a clean trace. Pick a specific sensor (e.g., `CAM-A1`) or intersection (e.g., `INT-A1`):

**Filter by sensor ID — see one sensor's journey across all VMs:**

```bash
# VM1: sensor published + broker forwarded
grep "CAM-A1" ~/traffic-system/data/vm1.log

# VM2: analytics received + decision + DB replica insert
grep "CAM-A1" ~/traffic-system/data/vm2.log

# VM3: primary DB insert
grep "CAM-A1" ~/traffic-system/data/vm3.log
```

**Filter by intersection — see everything happening at one intersection:**

```bash
# VM1: all sensors at INT-A1
grep "INT-A1" ~/traffic-system/data/vm1.log

# VM2: analytics + semaphore changes + DB inserts for INT-A1
grep "INT-A1" ~/traffic-system/data/vm2.log

# VM3: DB inserts for INT-A1
grep "INT-A1" ~/traffic-system/data/vm3.log
```

**Show only the last N lines (most recent events):**

```bash
grep "CAM-A1" ~/traffic-system/data/vm1.log | tail -5
grep "CAM-A1" ~/traffic-system/data/vm2.log | tail -5
grep "CAM-A1" ~/traffic-system/data/vm3.log | tail -5
```

**Live filtering — watch events in real-time (open a second terminal on each VM):**

```bash
# VM1: watch camera sensor events as they happen
tail -f ~/traffic-system/data/vm1.log | grep "CAM-A1"

# VM2: watch analytics processing CAM-A1 events
tail -f ~/traffic-system/data/vm2.log | grep "CAM-A1"

# VM3: watch primary DB receiving CAM-A1 inserts
tail -f ~/traffic-system/data/vm3.log | grep "CAM-A1"
```

### Demo script: trace one event end-to-end

After running the system for at least 30 seconds, run this on each VM to show a clean trace. It picks the **last 3 events** for sensor CAM-A1:

**On VM1:**
```bash
echo "=== VM1: Sensor Generation + Broker Forward ==="
grep "CAM-A1" ~/traffic-system/data/vm1.log | tail -3
echo ""
grep "FORWARD" ~/traffic-system/data/vm1.log | tail -3
```

**On VM2:**
```bash
echo "=== VM2: Analytics Processing + Replica DB Insert ==="
grep "CAM-A1" ~/traffic-system/data/vm2.log | tail -3
```

**On VM3:**
```bash
echo "=== VM3: Primary DB Insert ==="
grep "CAM-A1" ~/traffic-system/data/vm3.log | tail -3
```

### What should be seen

The timestamps prove the same event traveled across all 3 machines:

1. **VM1** at `T=0.000s`: `[CAM-A1 @ INT-A1] volumen=12, velocidad=25.3 km/h` — sensor generated
2. **VM1** at `T=0.001s`: `[FORWARD #7] topic=camara | size=198 bytes` — broker forwarded to VM2
3. **VM2** at `T=0.003s`: `[EVENT #7] CAM-A1 @ INT-A1 -> state=NORMAL, decision=NO_ACTION` — analytics processed
4. **VM2** at `T=0.005s`: `[INSERT sensor_event] CAM-A1 @ INT-A1 (CAMARA)` — replica DB stored
5. **VM3** at `T=0.008s`: `[INSERT sensor_event] CAM-A1 @ INT-A1 (CAMARA)` — primary DB stored

The same `CAM-A1` identifier appears on all 3 machines, proving the distributed PUB/SUB -> PUSH/PULL pipeline works.

### Additional useful filters

```bash
# See only semaphore state changes (congestion responses):
grep "NS:.*EW:" ~/traffic-system/data/vm2.log

# See only congestion detections:
grep "CONGESTION" ~/traffic-system/data/vm2.log

# See only green wave activations:
grep "GREEN_WAVE\|GREEN WAVE\|priority_action" ~/traffic-system/data/vm2.log

# See failover/recovery events:
grep "FAILOVER\|RECOVERY" ~/traffic-system/data/vm2.log

# See latency measurements:
grep "LATENCY" ~/traffic-system/data/vm2.log

# Count total events processed per VM:
grep -c "FORWARD" ~/traffic-system/data/vm1.log
grep -c "EVENT #" ~/traffic-system/data/vm2.log
grep -c "INSERT sensor_event" ~/traffic-system/data/vm3.log
```

---

## Updating Code Later

If you push new changes to the branch, update all VMs:

```bash
cd ~/traffic-system/app
git pull
source ~/traffic-system/venv/bin/activate
pip install -r requirements.txt -q
```
