# Intelligent Urban Traffic Management

A distributed platform for intelligent urban traffic management, built with **Python** and **ZeroMQ**. The system simulates a city grid with smart sensors, real-time analytics, automated semaphore control, and a monitoring interface -- all running across three networked machines communicating via asynchronous messaging patterns.

> **Course:** Introduction to Distributed Systems -- Pontificia Universidad Javeriana, 2026-10

---

## Project Description

The system simulates a city represented as a **4x4 grid** (rows A-D, columns 1-4) with 16 intersections. Each intersection can have traffic sensors and a semaphore. Three types of sensors generate real-time events:

| Sensor | Event Type | What It Measures |
|--------|-----------|-----------------|
| **Camera** (CAM) | Queue Length (Lq) | Vehicles waiting + average speed |
| **Inductive Loop** (ESP) | Vehicle Count (Cv) | Vehicles passing over the loop in a time interval |
| **GPS** | Traffic Density (Dt) | Average speed + derived congestion level (ALTA/NORMAL/BAJA) |

Sensor data flows through a ZeroMQ broker to an analytics engine that evaluates traffic rules, makes decisions (extend green, trigger green wave, etc.), controls semaphores, and persists all data to both a primary and replica database. A monitoring service provides an interactive CLI for queries and direct commands (e.g., prioritizing an ambulance route).

The system handles **fault tolerance** transparently: if the primary database machine (PC3) goes down, all operations automatically fail over to the replica on PC2.

---

## System Architecture

The system is distributed across **3 machines** (Docker containers), each with distinct responsibilities:

```
 COMPUTER 1 (PC1)                COMPUTER 2 (PC2)                  COMPUTER 3 (PC3)
 ========================       ==============================     =======================
                                                                  
  +------------------+          +------------------------+         +-------------------+
  | Camera Sensors   |--PUB--+  | Analytics Service      |         | Monitoring &      |
  | (8 cameras)      |       |  |  - Receives events     |         | Query Service     |
  +------------------+       |  |  - Evaluates rules     |  REQ/  |  - Interactive CLI |
                             |  |  - Makes decisions     |<-REP-->|  - Queries state   |
  +------------------+       |  |  - Sends DB writes     |        |  - Direct commands |
  | Inductive Loops  |--PUB--+  +---+----+----+----------+        +-------------------+
  | (8 loops)        |       |      |    |    |                           |
  +------------------+       |      |    |    |                           |
                             |      |    |   PUSH                    REQ/REP
  +------------------+       |      |    |    |                           |
  | GPS Sensors      |--PUB--+      |    |    v                           v
  | (8 sensors)      |       |      |    |  +-----------------+    +-------------------+
  +------------------+       |      |    |  | Semaphore       |    | Primary Database  |
                             |      |    |  | Control Service |    | (SQLite)          |
         PUB/SUB             |     PUSH  |  |  - State mgmt   |    +-------------------+
           |                 |      |    |  |  - Light changes |
           v                 |      |    |  +-----------------+
  +------------------+       |      |    |
  | ZMQ Broker       |  SUB  |      |   PUSH
  |  - Subscribes to |<------+      |    |
  |    all sensors   |              |    v
  |  - Forwards to   |--PUB------->| +-----------------+
  |    PC2           |      SUB    | | DB Replica      |
  +------------------+             | | (SQLite backup) |
                                   | +-----------------+
                                   |
                                   v
                              PUSH/PULL to
                              both databases
```

### ZMQ Communication Patterns

| Connection | Pattern | Direction | Purpose |
|-----------|---------|-----------|---------|
| Sensors --> Broker | **PUB/SUB** | PC1 internal | Sensors publish events, broker subscribes |
| Broker --> Analytics | **PUB/SUB** | PC1 --> PC2 | Broker forwards all events to analytics |
| Analytics --> Semaphore Control | **PUSH/PULL** | PC2 internal | Semaphore change commands |
| Analytics --> Primary DB | **PUSH/PULL** | PC2 --> PC3 | Persist sensor data and decisions |
| Analytics --> Replica DB | **PUSH/PULL** | PC2 internal | Replicate data for fault tolerance |
| Monitoring <--> Analytics | **REQ/REP** | PC3 --> PC2 | Queries and direct commands |
| Health Check | **REQ/REP** | PC2 --> PC3 | Periodic heartbeat to detect PC3 failure |

---

## Traffic Rules

The analytics service evaluates sensor data against these rules to determine the traffic state at each intersection:

| State | Condition | Action | Timing |
|-------|-----------|--------|--------|
| **Normal** | Q < 5 AND Vp > 35 AND D < 20 | Standard semaphore cycle | 15s red/green |
| **Congestion** | Q >= 10 OR Vp <= 20 OR D >= 40 | Extend green phase on congested direction | +10s green extension |
| **Green Wave** | User command (e.g., ambulance) | Force all semaphores on a route to GREEN | 30s forced green |

**Variables:**
- **Q** -- Queue length: number of vehicles waiting (from camera sensor)
- **Vp** -- Average speed in km/h (from camera and GPS sensors)
- **D** -- Traffic density in vehicles/km (derived from GPS sensor)

**GPS Congestion Levels:**
- **ALTA** (High): average speed < 10 km/h
- **NORMAL**: 11 <= average speed <= 39 km/h
- **BAJA** (Low): average speed > 40 km/h

---

## How to Run

### Prerequisites

- **Docker** and **Docker Compose** (recommended)
- **Python 3.11+** (for local development)
- **Git**

### Using Docker Compose (Recommended)

This launches all three machines as containers on a shared network:

```bash
# Build and start the entire system
docker compose up --build

# Run in detached mode
docker compose up --build -d

# View logs for a specific container
docker compose logs -f pc1-sensors-broker
docker compose logs -f pc2-analytics
docker compose logs -f pc3-monitoring

# Stop the system
docker compose down

# Stop and remove volumes (clears databases)
docker compose down -v
```

#### Accessing the Monitoring CLI

The monitoring service on PC3 provides an interactive CLI. To attach to it:

```bash
docker attach pc3-monitoring
```

#### Switching Broker Mode

To run with the multithreaded broker (for performance experiments), set the environment variable in `docker-compose.yml` or pass it directly:

```bash
# Add to pc1 service in docker-compose.yml:
#   environment:
#     - BROKER_MODE=threaded
#     - SENSOR_INTERVAL=5

# Or override at runtime:
docker compose run -e BROKER_MODE=threaded -e SENSOR_INTERVAL=5 pc1
```

### Running Locally (Development)

For local development and debugging without Docker:

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set PYTHONPATH to project root
# Linux/macOS:
export PYTHONPATH=$(pwd)
# Windows (PowerShell):
$env:PYTHONPATH = (Get-Location).Path

# 3. Start PC1 (sensors + broker)
python pc1/start_pc1.py

# Or start individual components:
python -m pc1.broker --mode standard
python -m pc1.sensors.camera_sensor --all --interval 10
python -m pc1.sensors.inductive_sensor --all --interval 30
python -m pc1.sensors.gps_sensor --all --interval 10

# 4. Start PC2 (analytics + semaphore control + replica DB)
python pc2/start_pc2.py

# 5. Start PC3 (monitoring CLI + primary DB)
python pc3/start_pc3.py
```

#### Running a Single Sensor

Each sensor supports both `--all` (all sensors of that type from config) and single-sensor mode:

```bash
# Single camera sensor at intersection INT-A1
python -m pc1.sensors.camera_sensor --sensor-id CAM-A1 --intersection INT-A1 --interval 10

# Single GPS sensor
python -m pc1.sensors.gps_sensor --sensor-id GPS-B2 --intersection INT-B2 --interval 10

# Single inductive loop (30s default interval)
python -m pc1.sensors.inductive_sensor --sensor-id ESP-C4 --intersection INT-C4
```

### Running Tests

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run all tests
pytest tests/ -v

# Run with coverage (if installed)
pytest tests/ -v --tb=short
```

---

## Configuration

All system parameters are defined in `config/city_config.json`. This single file controls the entire system behavior.

### City Grid

```json
"city": {
  "grid": {
    "rows": ["A", "B", "C", "D"],
    "columns": [1, 2, 3, 4]
  }
}
```

This creates 16 intersections: `INT-A1` through `INT-D4`. To change the grid size, modify the rows and columns arrays and update the `intersections`, `sensors`, and `semaphores` sections accordingly.

### Sensor Mappings

Each sensor entry maps a sensor ID to an intersection:

```json
"sensors": {
  "cameras": [
    {"sensor_id": "CAM-A1", "interseccion": "INT-A1"},
    ...
  ],
  "inductive_loops": [
    {"sensor_id": "ESP-A2", "interseccion": "INT-A2"},
    ...
  ],
  "gps": [
    {"sensor_id": "GPS-A1", "interseccion": "INT-A1"},
    ...
  ]
}
```

The default configuration has 8 sensors of each type (24 total) distributed across the 16 intersections.

### Timing Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `normal_cycle_sec` | 15 | Standard red/green cycle duration |
| `congestion_extension_sec` | 10 | Extra green time during congestion |
| `green_wave_duration_sec` | 30 | Duration of forced green wave |
| `sensor_default_interval_sec` | 10 | Camera and GPS event generation interval |
| `inductive_interval_sec` | 30 | Inductive loop measurement interval |
| `health_check_interval_sec` | 5 | How often PC2 pings PC3 |
| `health_check_timeout_ms` | 2000 | Timeout for each health check ping |
| `health_check_max_retries` | 3 | Failed pings before declaring PC3 down |

### ZMQ Ports

| Port | Name | Used By |
|------|------|---------|
| 5555 | `sensor_camera_pub` | Camera sensors PUB |
| 5556 | `sensor_inductive_pub` | Inductive sensors PUB |
| 5557 | `sensor_gps_pub` | GPS sensors PUB |
| 5560 | `broker_pub` | Broker PUB (to PC2) |
| 5561 | `analytics_rep` | Analytics REP (from PC3 monitoring) |
| 5562 | `semaphore_control_pull` | Semaphore control PULL |
| 5563 | `db_primary_pull` | Primary DB PULL (on PC3) |
| 5564 | `db_replica_pull` | Replica DB PULL (on PC2) |
| 5565 | `health_check_rep` | Health check REP (on PC3) |

### Network Hosts

```json
"network": {
  "pc1_host": "pc1",
  "pc2_host": "pc2",
  "pc3_host": "pc3"
}
```

These match the Docker Compose service names. For local development, change them to `localhost` or `127.0.0.1`.

---

## Fault Tolerance

The system is designed to handle the failure of PC3 (primary database + monitoring) transparently.

### How It Works

```
Normal Operation:
  Analytics (PC2) --PUSH--> Primary DB (PC3)    [writes go to both]
  Analytics (PC2) --PUSH--> Replica DB (PC2)

  Health Check:
  Analytics (PC2) --REQ "PING"--> PC3
  Analytics (PC2) <--REP "PONG"-- PC3            [every 5 seconds]

PC3 Failure Detected:
  Analytics (PC2) --REQ "PING"--> PC3
  Analytics (PC2) ... timeout (2s) ... retry x3
  [FAILOVER] PC3 is down. Using replica DB on PC2.

After Failover:
  Analytics (PC2) --PUSH--> Replica DB (PC2)     [writes only to replica]
  Monitoring queries redirected to Replica DB
```

### Detection Mechanism

1. The analytics service on PC2 sends a `PING` heartbeat to PC3 every **5 seconds**
2. If no `PONG` response is received within **2 seconds**, it counts as a failed attempt
3. After **3 consecutive failures** (configurable), PC3 is declared down
4. All database writes are redirected exclusively to the PC2 replica
5. Monitoring queries are served from the replica database
6. The system continues operating without interruption

### Testing Failover

```bash
# Start the full system
docker compose up -d

# Simulate PC3 failure
docker stop pc3-monitoring

# Observe failover in PC2 logs
docker compose logs -f pc2-analytics
# Should show: [FAILOVER] PC3 is down. Using replica DB on PC2.

# Verify system continues operating
# (sensors still generate data, analytics still processes, DB replica still receives writes)

# Restore PC3
docker start pc3-monitoring
```

---

## Performance Testing

The project requires comparing two broker designs under different load conditions.

### Experiment Design

| Scenario | Sensors | Generation Interval | Broker Design |
|----------|---------|-------------------|---------------|
| **1A** | 1 of each type (3 total) | 10 seconds | Standard (single-thread) |
| **1B** | 1 of each type (3 total) | 10 seconds | Multithreaded |
| **2A** | 2 of each type (6 total) | 5 seconds | Standard (single-thread) |
| **2B** | 2 of each type (6 total) | 5 seconds | Multithreaded |

### Variables Measured

**Dependent variables (what we measure):**
- **Throughput**: Number of events stored in the database within a 2-minute window
- **Latency**: Time from when a user sends a semaphore change command to when the semaphore actually changes state

**Independent variables (what we control):**
- Number of sensors generating data
- Time interval between event generations
- Broker architecture (single-threaded vs multithreaded)

### Running Experiments

```bash
# Scenario 1A: 1 sensor per type, 10s interval, standard broker
docker compose run -e BROKER_MODE=standard -e SENSOR_INTERVAL=10 pc1

# Scenario 1B: 1 sensor per type, 10s interval, threaded broker
docker compose run -e BROKER_MODE=threaded -e SENSOR_INTERVAL=10 pc1

# Scenario 2A: 2 sensors per type, 5s interval, standard broker
docker compose run -e BROKER_MODE=standard -e SENSOR_INTERVAL=5 pc1

# Scenario 2B: 2 sensors per type, 5s interval, threaded broker
docker compose run -e BROKER_MODE=threaded -e SENSOR_INTERVAL=5 pc1
```

### Broker Modes

- **Standard** (`BROKER_MODE=standard`): Single-threaded broker using `zmq.Poller` to monitor all three sensor SUB sockets in a loop. Simple and predictable.

- **Threaded** (`BROKER_MODE=threaded`): Each sensor topic gets its own subscriber thread. Threads forward messages via an `inproc://` PUSH/PULL pipeline to a collector thread that publishes to PC2. Higher concurrency but more overhead.

### Expected Analysis

After running all 4 scenarios, collect results in tables, generate graphs (throughput and latency as functions of sensor count and interval), and analyze:
- Which design handles higher load better?
- How does latency scale with increased sensor count?
- Which architecture is more scalable and why?
