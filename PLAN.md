# Development Plan: Gestion Inteligente de Trafico Urbano

## Technology Stack (All Open Source)

| Component | Technology | License |
|-----------|-----------|---------|
| Language | **Python 3.11+** | PSF |
| Messaging | **pyzmq** (ZeroMQ bindings) | LGPL/BSD |
| Database | **SQLite3** (built into Python) | Public Domain |
| Containers | **Docker + Docker Compose** | Apache 2.0 |
| Serialization | **JSON** (stdlib) | - |
| Config | **JSON** (stdlib `json`) | - |
| Logging | Python `logging` (stdlib) | - |
| Testing/Metrics | `time` (stdlib), `pytest` | MIT |

---

## Project Structure

```
distribuidos/
├── config/
│   └── city_config.json          # Grid size, sensors, rules, timings, ports, network
├── common/
│   ├── __init__.py
│   ├── models.py                 # Data classes for events, commands
│   ├── constants.py              # Ports, topics, congestion thresholds
│   ├── config_loader.py          # Config singleton loader
│   └── db_utils.py               # SQLite helper (create tables, insert, query)
├── pc1/
│   ├── __init__.py
│   ├── sensors/
│   │   ├── __init__.py
│   │   ├── camera_sensor.py      # PUB - Queue length events (Lq)
│   │   ├── inductive_sensor.py   # PUB - Vehicle count events (Cv)
│   │   └── gps_sensor.py         # PUB - Traffic density events (Dt)
│   ├── broker.py                 # SUB (from sensors) -> PUB (to PC2) - standard
│   ├── broker_threaded.py        # Multithreaded broker variant (inproc PUSH/PULL)
│   └── start_pc1.py              # Launcher: broker + all sensors as subprocesses
├── pc2/
│   ├── __init__.py
│   ├── analytics_service.py      # SUB (from broker) + REP (from PC3) + PUSH (to DBs)
│   ├── traffic_light_control.py  # Receives commands, manages semaphore states
│   ├── db_replica.py             # PULL - receives data for replica SQLite DB
│   ├── health_checker.py         # FailoverState + health check daemon thread
│   ├── monitoring_fallback.py    # Fallback monitoring CLI on PC2 during failover
│   └── start_pc2.py              # Launcher: db_replica + semaphore + analytics
├── pc3/
│   ├── __init__.py
│   ├── monitoring_service.py     # REQ/REP queries + direct commands to analytics
│   ├── db_primary.py             # PULL - receives data for primary SQLite DB + health REP
│   └── start_pc3.py              # Launcher: db_primary subprocess + monitoring foreground
├── docker-compose.yml
├── Dockerfile.pc1
├── Dockerfile.pc2
├── Dockerfile.pc3
├── .dockerignore
├── requirements.txt
├── requirements-dev.txt          # Extends requirements.txt + ruff
├── ruff.toml                     # Linter/formatter configuration
├── .github/
│   └── workflows/
│       └── ci.yml                # CI pipeline: ruff check + format + pytest
├── PLAN.md                       # This file - master development plan
├── phase3_plan.md                # Phase 3 design decisions
├── phase4_plan.md                # Phase 4 design decisions
├── phase5_plan.md                # Phase 5 design decisions
├── README.md                     # Project readme with usage instructions
└── tests/
    ├── test_common.py            # 25 tests - models, config, DB utils
    ├── test_sensors.py           # 19 tests - sensor event generation
    ├── test_analytics.py         # 32 tests - analytics, rules, semaphore control
    ├── test_monitoring.py        # 26 tests - DB primary, monitoring CLI, health check
    └── test_failover.py          # 15 tests - failover state, health checker, recovery
```

---

## Step-by-Step Development Plan

### PHASE 1: Foundation & Configuration (Days 1-2) ✅ COMPLETED

#### Step 1.1 - Project scaffolding
- [x] Create the directory structure above
- [x] Create `requirements.txt` with: `pyzmq`, `pyyaml`, `pytest`
- [x] Create base Dockerfiles for each PC

#### Step 1.2 - Configuration system (`config/city_config.json`)
- [x] Define the 4x4 grid (rows A-D, columns 1-4)
- [x] Map which intersections have which sensors
- [x] Define ZMQ ports and addresses
- [x] Define traffic rules and thresholds

Example config (see `config/city_config.json` for the full version):
```json
{
  "city": {
    "name": "Ciudad Simulada",
    "grid": { "rows": ["A","B","C","D"], "columns": [1,2,3,4] }
  },
  "sensors": {
    "cameras": [{"sensor_id": "CAM-A1", "interseccion": "INT-A1"}, ...],
    "inductive_loops": [{"sensor_id": "ESP-A2", "interseccion": "INT-A2"}, ...],
    "gps": [{"sensor_id": "GPS-A1", "interseccion": "INT-A1"}, ...]
  },
  "rules": {
    "normal": { "conditions": { "Q_max": 5, "Vp_min": 35, "D_max": 20 } },
    "congestion": { "conditions": { "Q_min": 10, "Vp_max": 20, "D_min": 40 } },
    "green_wave": { "conditions": { "trigger": "user_command" } }
  },
  "timings": {
    "normal_cycle_sec": 15,
    "congestion_extension_sec": 10,
    "green_wave_duration_sec": 30,
    "sensor_default_interval_sec": 10,
    "inductive_interval_sec": 30,
    "health_check_interval_sec": 5,
    "health_check_timeout_ms": 2000,
    "health_check_max_retries": 3
  },
  "zmq_ports": {
    "sensor_camera_pub": 5555,
    "sensor_inductive_pub": 5556,
    "sensor_gps_pub": 5557,
    "broker_pub": 5560,
    "analytics_rep": 5561,
    "semaphore_control_pull": 5562,
    "db_primary_pull": 5563,
    "db_replica_pull": 5564,
    "health_check_rep": 5565
  },
  "network": {
    "pc1_host": "pc1",
    "pc2_host": "pc2",
    "pc3_host": "pc3"
  }
}
```

#### Step 1.3 - Common models (`common/models.py`)
- [x] Define data classes for: `CameraEvent`, `InductiveEvent`, `GPSEvent`, `SemaphoreCommand`, `MonitoringQuery`, `MonitoringResponse`
- [x] JSON serialization/deserialization helpers
- [x] Config loader utility (`common/config_loader.py`)

#### Step 1.4 - Database schema (`common/db_utils.py`)
- [x] Create tables: `sensor_events`, `semaphore_states`, `congestion_history`, `priority_actions`
- [x] Helper functions: `insert_event()`, `query_by_time_range()`, `query_by_intersection()`, `get_semaphore_state()`
- [x] Performance metric helper: `get_event_count_in_interval()`
- [x] System summary: `get_system_summary()`

---

### PHASE 2: PC1 - Sensors & Broker (Days 3-5) ✅ COMPLETED

#### Step 2.1 - Sensor simulators
Each sensor type runs as a single process handling all sensors of that type via a shared ZMQ `PUB` socket:

- [x] **Camera sensor** (`camera_sensor.py`): Publishes on topic `"camara"`. Generates random `volumen` (0-20 vehicles) and `velocidad_promedio` (5-50 km/h) at configured intervals.
- [x] **Inductive loop sensor** (`inductive_sensor.py`): Publishes on topic `"espira"`. Generates random `vehiculos_contados` (0-30) every 30 seconds.
- [x] **GPS sensor** (`gps_sensor.py`): Publishes on topic `"gps"`. Generates random `velocidad_promedio` and derives `nivel_congestion` (ALTA < 10, NORMAL 10-40, BAJA > 40).

All sensors:
- Accept CLI params or read config for: intersection ID, generation interval
- Print each generated event to stdout
- Attach proper timestamps

**Sensor event formats:**

Camera event (EVENTO_LONGITUD_COLA - Lq):
```json
{
  "sensor_id": "CAM-C5",
  "tipo_sensor": "camara",
  "interseccion": "INT-C5",
  "volumen": 10,
  "velocidad_promedio": 25,
  "timestamp": "2026-02-09T15:10:00Z"
}
```

Inductive loop event (EVENTO_CONTEO_VEHICULAR - Cv):
```json
{
  "sensor_id": "ESP-C5",
  "tipo_sensor": "espira_inductiva",
  "interseccion": "INT-C5",
  "vehiculos_contados": 12,
  "intervalo_segundos": 30,
  "timestamp_inicio": "2026-02-09T15:20:00Z",
  "timestamp_fin": "2026-02-09T15:20:30Z"
}
```

GPS event (EVENTO_DENSIDAD_DE_TRAFICO - Dt):
```json
{
  "sensor_id": "GPS-C5",
  "nivel_congestion": "ALTA",
  "velocidad_promedio": 18,
  "timestamp": "2026-02-09T15:20:10Z"
}
```

Congestion level rules for GPS:
- ALTA: velocidad_promedio < 10
- NORMAL: 10 <= velocidad_promedio <= 40
- BAJA: velocidad_promedio > 40

#### Step 2.2 - ZMQ Broker (`broker.py`)
- [x] **SUB** socket subscribing to topics: `"camara"`, `"espira"`, `"gps"` (from local sensors)
- [x] **PUB** socket forwarding all received events to PC2
- [x] Uses zmq.Poller for efficient multi-socket polling
- [x] Print forwarded messages to stdout

#### Step 2.3 - Multithreaded broker variant (`broker_threaded.py`)
- [x] Same broker but using `threading` to handle each topic subscription in a separate thread
- [x] Uses inproc PUSH/PULL pattern to safely share messages between threads
- [x] This is needed for the performance experiments in the final delivery

#### Step 2.4 - PC1 Launcher (`start_pc1.py`)
- [x] Entrypoint script that launches broker + all sensor processes as subprocesses
- [x] Supports `--broker-mode standard|threaded` and `--interval` override
- [x] Graceful shutdown: terminates all children on SIGINT/SIGTERM
- [x] Environment variable support: `BROKER_MODE`, `SENSOR_INTERVAL`

---

### PHASE 3: PC2 - Analytics & Semaphore Control (Days 5-8) ✅ COMPLETED

#### Step 3.1 - Analytics service (`analytics_service.py`)
This is the core brain of the system. Multiple ZMQ sockets:

- [x] **SUB** socket: Subscribes to broker's PUB (receives all sensor events)
- [x] **PUSH** socket (x2): Sends data to primary DB (PC3) and replica DB (PC2)
- [x] **PUSH** to semaphore control: Sends light-change commands (PUSH/PULL pattern)
- [x] **REP** socket: Responds to monitoring queries from PC3 (REQ/REP)

Logic:
1. Receive sensor event
2. Evaluate rules per intersection:
   - **Normal traffic**: `Q < 5 AND Vp > 35 AND D < 20` -> standard 15s red cycle
   - **Congestion detected**: `Q >= 10 OR Vp < 20 OR D >= 40` -> extend green by 10s on congested direction
   - **Green wave (priority)**: User-triggered via monitoring -> force all semaphores on a route to green
3. If state change needed, send command to semaphore control service
4. PUSH event data to both databases
5. Print all decisions and actions to stdout

#### Step 3.2 - Semaphore control service (`traffic_light_control.py`)
- [x] Maintains in-memory state of all semaphores (intersection -> color)
- [x] **PULL** or **SUB** socket: Receives commands from analytics
- [x] Executes light changes: red->green, green->red
- [x] Prints every state change: `"[INT-B3] RED -> GREEN (reason: congestion detected)"`

#### Step 3.3 - DB Replica worker (`db_replica.py`)
- [x] **PULL** socket: Receives data from analytics service
- [x] Inserts into local SQLite replica database
- [x] This replica activates as primary if PC3 fails

---

### PHASE 4: PC3 - Primary DB & Monitoring (Days 8-10) ✅ COMPLETED

#### Step 4.1 - Primary DB worker (`db_primary.py`)
- [x] **PULL** socket: Receives data from analytics service (PC2)
- [x] Inserts into primary SQLite database
- [x] Prints operations to stdout
- [x] Health check REP daemon thread (port 5565) for Phase 5

#### Step 4.2 - Monitoring & query service (`monitoring_service.py`)
- [x] Interactive CLI for the user
- [x] **REQ** socket to analytics service (PC2) for queries and commands

**Supported operations:**
1. Estado actual de una interseccion (e.g., INT-B3)
2. Historial de congestion entre dos timestamps
3. Forzar ola verde en una via (ambulancia)
4. Cambiar semaforo de una interseccion especifica
5. Estado general del sistema
6. Health check

- [x] Prints all operations and responses

#### Step 4.3 - PC3 Launcher (`start_pc3.py`)
- [x] Launches db_primary as background subprocess
- [x] Runs monitoring_service in foreground for interactive stdin access
- [x] Graceful shutdown of background process

---

### PHASE 5: Fault Tolerance (Days 10-12) ✅ COMPLETED

#### Step 5.1 - Health check mechanism
- [x] PC2's analytics service periodically sends a heartbeat **REQ** to PC3's DB
- [x] If no response after N attempts (e.g., 3 retries with 2s timeout), declare PC3 as failed
- [x] Implement using ZMQ `RCVTIMEO` socket option (Lazy Pirate pattern with socket recreation)

#### Step 5.2 - Failover logic
- [x] When PC3 failure detected:
  1. Analytics redirects all PUSH writes to PC2's replica only
  2. PUSH socket to PC3 is disconnected to prevent ZMQ buffering
  3. Print alert: `"[FAILOVER] PC3 is down. Using replica DB on PC2."`
- [x] Operation continues transparently
- [x] Fallback monitoring CLI on PC2 (`monitoring_fallback.py`)
- [x] Automatic recovery when PC3 comes back (health checker detects PONG)
- [x] **Separate ZMQ context** for PC3-bound sockets: When PC3's Docker container stops, DNS resolution blocks ZMQ I/O threads. Using a dedicated `pc3_context` isolates these from the main context's SUB/REP sockets
- [x] **Thread-safe `_send_to_primary()` callable**: `push_to_dbs()` accepts `Callable[[str], None] | None` instead of raw socket. The callable holds a lock during send so the health checker's failover callback can't close the socket mid-send. Uses `zmq.NOBLOCK` to never block the main loop

#### Step 5.3 - Testing failover
- [x] 15 unit tests covering FailoverState, HealthChecker, push_to_dbs, full integration
- [x] All 117 tests passing (`pytest tests/ -v`)

---

### PHASE 6: Docker & Networking (Days 12-13) ✅ COMPLETED

#### Step 6.1 - Docker Compose setup
```yaml
services:
  pc1:
    build: { context: ., dockerfile: Dockerfile.pc1 }
    container_name: pc1-sensors-broker
    networks: [traffic-net]
    depends_on:
      pc2: { condition: service_started }
    environment:
      - BROKER_MODE=${BROKER_MODE:-standard}
      - SENSOR_INTERVAL=${SENSOR_INTERVAL:-0}
    restart: unless-stopped
  pc2:
    build: { context: ., dockerfile: Dockerfile.pc2 }
    container_name: pc2-analytics
    networks: [traffic-net]
    volumes: [pc2-data:/data]
    restart: unless-stopped
  pc3:
    build: { context: ., dockerfile: Dockerfile.pc3 }
    container_name: pc3-monitoring
    networks: [traffic-net]
    volumes: [pc3-data:/data]
    depends_on:
      pc2: { condition: service_started }
    stdin_open: true
    tty: true
    restart: unless-stopped

networks:
  traffic-net:
    driver: bridge

volumes:
  pc2-data:
  pc3-data:
```

#### Step 6.2 - Dockerfiles
- [x] Based on `python:3.11-slim`
- [x] Install `pyzmq`
- [x] Copy respective PC code + common modules
- [x] Entrypoint scripts that launch all processes for that PC
- [x] Build and verify all 3 images
- [x] Integration test: docker compose up, verify cross-container ZMQ communication
- [x] Failover test: docker stop pc3, verify system continues on replica
- [x] Recovery test: docker start pc3, verify auto-recovery

---

### PHASE 7: Performance Testing & Comparison (Days 13-15)

#### Step 7.1 - Instrumentation
- [ ] Add timestamps to measure:
  - **Throughput**: Count DB inserts in a 2-minute window
  - **Latency**: Time from user command to semaphore state change
- [ ] Use Python `time.perf_counter()` for precision

#### Step 7.2 - Run experiments (Table 1 from project spec)

| Scenario | Sensors | Interval | Design |
|----------|---------|----------|--------|
| 1A | 1 of each type (3 total) | 10s | Standard broker |
| 1B | 1 of each type (3 total) | 10s | Multithreaded broker |
| 2A | 2 of each type (6 total) | 5s | Standard broker |
| 2B | 2 of each type (6 total) | 5s | Multithreaded broker |

**Variables to measure (dependent):**
- Cantidad de solicitudes almacenadas en la BD en un intervalo de 2 minutos
- Tiempo desde que el usuario solicita una accion hasta que el semaforo cambia

**Variables independientes (factors):**
- Numero de sensores generando informacion
- Tiempo entre generacion de mediciones

#### Step 7.3 - Collect & analyze
- [ ] Record results in tables
- [ ] Create graphs (matplotlib or export CSV)
- [ ] Analyze: which design scales better and why

---

### PHASE 8: Documentation & Deliverables (Parallel throughout)

#### First Delivery (Week 10) - 15%
- [ ] System models: architectural, interaction, fault, security + McCuber cube
- [ ] UML diagrams: deployment, component, class, sequence
- [ ] Explanation of:
  - a) How processes obtain initial resource definitions (sensors, grid size, semaphores)
  - b) Traffic rules for 3 states
  - c) Query types available to users
  - d) Examples of direct commands from Monitoring to Analytics
- [ ] Test protocol document
- [ ] Performance metrics methodology
- [ ] Working implementation: PC1 + PC2 services + DB writes to PC3
- [ ] Source code of implemented functionality

#### Second Delivery (Week 17) - 25%
- [ ] Complete working system across 3 Docker containers
- [ ] Source code (.zip) + README with execution instructions
- [ ] Complement first delivery documentation
- [ ] Documented source files
- [ ] 10-minute demo video covering:
  - a) Component distribution across machines
  - b) Parameters for all process types
  - c) City grid distribution among sensors and semaphore assignment
  - d) Libraries and patterns used
  - e) Fault handling
- [ ] Performance report (max 5 pages): hw/sw specs, measurement tools, tables, graphs, analysis

---

## ZMQ Communication Patterns Summary

```
Sensor (PUB) --[camara/espira/gps]--> Broker (SUB/PUB) --[all topics]--> Analytics (SUB)
                                                                              |
                                                           PUSH/PULL --------+-----> DB Primary (PC3)
                                                           PUSH/PULL --------+-----> DB Replica (PC2)
                                                           PUSH/PULL --------+-----> Semaphore Control (PC2)
                                                           REP <---REQ-------+-----> Monitoring (PC3)
                                                           REQ/REP ----------+-----> Health Check (PC3)
```

## Traffic Rules (Proposed)

| State | Condition | Action |
|-------|-----------|--------|
| **Normal** | Q < 5 AND Vp > 35 AND D < 20 | Standard 15s red/green cycle |
| **Congestion** | Q >= 10 OR Vp <= 20 OR D >= 40 | Extend green +10s on congested direction, reduce cross-street green |
| **Green Wave** | User command (ambulance) | Force all semaphores on specified route to GREEN for 30s |

## System Assumptions
- All roads are one-way
- Semaphore changes are only green->red and red->green (no yellow)
- City grid is 4x4 (rows A-D, columns 1-4) = 16 intersections
- Normal red-to-green wait time: 15 seconds

## Estimated Development Timeline

| Phase | Days | Description |
|-------|------|-------------|
| 1 | 1-2 | Foundation, config, models, DB schema |
| 2 | 3-5 | PC1: Sensors + Broker |
| 3 | 5-8 | PC2: Analytics + Semaphore Control + Replica |
| 4 | 8-10 | PC3: Primary DB + Monitoring CLI |
| 5 | 10-12 | Fault tolerance (health check + failover) |
| 6 | 12-13 | Docker Compose integration |
| 7 | 13-15 | Performance experiments |
| 8 | Ongoing | Documentation & deliverables |
