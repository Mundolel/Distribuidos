# Development Plan: Gestion Inteligente de Trafico Urbano

## Technology Stack (All Open Source)

| Component | Technology | License |
|-----------|-----------|---------|
| Language | **Python 3.11+** | PSF |
| Messaging | **pyzmq** (ZeroMQ bindings) | LGPL/BSD |
| Database | **SQLite3** (built into Python) | Public Domain |
| Containers | **Docker + Docker Compose** | Apache 2.0 |
| Serialization | **JSON** (stdlib) | - |
| Config | **YAML** via `pyyaml` or JSON | MIT |
| Logging | Python `logging` (stdlib) | - |
| Testing/Metrics | `time` (stdlib), `pytest` | MIT |

---

## Project Structure

```
distribuidos/
├── config/
│   └── city_config.json          # Grid size, sensors, rules, timings
├── common/
│   ├── __init__.py
│   ├── models.py                 # Data classes for events, commands
│   ├── constants.py              # Ports, topics, congestion thresholds
│   └── db_utils.py               # SQLite helper (create tables, insert, query)
├── pc1/
│   ├── sensors/
│   │   ├── camera_sensor.py      # PUB - Queue length events (Lq)
│   │   ├── inductive_sensor.py   # PUB - Vehicle count events (Cv)
│   │   └── gps_sensor.py         # PUB - Traffic density events (Dt)
│   └── broker.py                 # SUB (from sensors) -> PUB (to PC2)
├── pc2/
│   ├── analytics_service.py      # SUB (from broker) + REP (from PC3) + PUSH (to DB)
│   ├── traffic_light_control.py  # Receives commands, manages semaphore states
│   └── db_replica.py             # PULL - receives data for replica SQLite DB
├── pc3/
│   ├── monitoring_service.py     # REQ/REP queries + direct commands to analytics
│   └── db_primary.py             # PULL - receives data for primary SQLite DB
├── docker-compose.yml
├── Dockerfile.pc1
├── Dockerfile.pc2
├── Dockerfile.pc3
├── requirements.txt
└── tests/
    ├── test_sensors.py
    ├── test_analytics.py
    ├── test_failover.py
    └── test_performance.py
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

Example config:
```json
{
  "grid": { "rows": ["A","B","C","D"], "columns": [1,2,3,4] },
  "sensors": {
    "cameras": ["INT-A1","INT-B2","INT-C3","INT-D4"],
    "inductive": ["INT-A2","INT-B3","INT-C4"],
    "gps": ["INT-A3","INT-B4","INT-C1"]
  },
  "rules": {
    "normal": { "Q_max": 5, "Vp_min": 35, "D_max": 20 },
    "congestion": { "Q_min": 10, "Vp_max": 20, "D_min": 40 },
    "green_wave": { "priority": true }
  },
  "timings": {
    "normal_red_duration_sec": 15,
    "congestion_green_extension_sec": 10,
    "sensor_interval_sec": 10
  },
  "zmq_ports": {
    "sensor_pub": 5555,
    "broker_pub": 5556,
    "analytics_pull": 5557,
    "db_primary_pull": 5558,
    "db_replica_pull": 5559,
    "monitoring_req": 5560,
    "semaphore_control": 5561
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
- [x] **GPS sensor** (`gps_sensor.py`): Publishes on topic `"gps"`. Generates random `velocidad_promedio` and derives `nivel_congestion` (ALTA < 10, NORMAL 11-39, BAJA > 40).

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
- NORMAL: 11 <= velocidad_promedio <= 39
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

### PHASE 3: PC2 - Analytics & Semaphore Control (Days 5-8)

#### Step 3.1 - Analytics service (`analytics_service.py`)
This is the core brain of the system. Multiple ZMQ sockets:

- [ ] **SUB** socket: Subscribes to broker's PUB (receives all sensor events)
- [ ] **PUSH** socket (x2): Sends data to primary DB (PC3) and replica DB (PC2)
- [ ] **PUB/PUSH** to semaphore control: Sends light-change commands
- [ ] **REP** socket: Responds to monitoring queries from PC3 (REQ/REP)

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
- [ ] Maintains in-memory state of all semaphores (intersection -> color)
- [ ] **PULL** or **SUB** socket: Receives commands from analytics
- [ ] Executes light changes: red->green, green->red
- [ ] Prints every state change: `"[INT-B3] RED -> GREEN (reason: congestion detected)"`

#### Step 3.3 - DB Replica worker (`db_replica.py`)
- [ ] **PULL** socket: Receives data from analytics service
- [ ] Inserts into local SQLite replica database
- [ ] This replica activates as primary if PC3 fails

---

### PHASE 4: PC3 - Primary DB & Monitoring (Days 8-10)

#### Step 4.1 - Primary DB worker (`db_primary.py`)
- [ ] **PULL** socket: Receives data from analytics service (PC2)
- [ ] Inserts into primary SQLite database
- [ ] Prints operations to stdout

#### Step 4.2 - Monitoring & query service (`monitoring_service.py`)
- [ ] Interactive CLI for the user
- [ ] **REQ** socket to analytics service (PC2) for queries and commands

**Supported operations:**
1. Estado actual de una interseccion (e.g., INT-B3)
2. Historial de congestion entre dos timestamps
3. Forzar ola verde en una via (ambulancia)
4. Cambiar semaforo de una interseccion especifica
5. Estado general del sistema

- [ ] Also uses **REQ** to query database for historical data
- [ ] Prints all operations and responses

---

### PHASE 5: Fault Tolerance (Days 10-12)

#### Step 5.1 - Health check mechanism
- [ ] PC2's analytics service periodically sends a heartbeat **REQ** to PC3's DB
- [ ] If no response after N attempts (e.g., 3 retries with 2s timeout), declare PC3 as failed
- [ ] Implement using ZMQ `RCVTIMEO` socket option

#### Step 5.2 - Failover logic
- [ ] When PC3 failure detected:
  1. Analytics redirects all PUSH writes to PC2's replica only
  2. Monitoring queries are redirected to PC2's replica DB
  3. Print alert: `"[FAILOVER] PC3 is down. Using replica DB on PC2."`
- [ ] Operation continues transparently
- [ ] When PC3 comes back, resync mechanism (optional but recommended)

#### Step 5.3 - Testing failover
- [ ] Run system normally, then `docker stop pc3`
- [ ] Verify system continues operating using replica
- [ ] Verify queries still return correct data

---

### PHASE 6: Docker & Networking (Days 12-13)

#### Step 6.1 - Docker Compose setup
```yaml
services:
  pc1:
    build: { context: ., dockerfile: Dockerfile.pc1 }
    networks: [traffic-net]
  pc2:
    build: { context: ., dockerfile: Dockerfile.pc2 }
    networks: [traffic-net]
    volumes: [pc2-data:/data]
  pc3:
    build: { context: ., dockerfile: Dockerfile.pc3 }
    networks: [traffic-net]
    volumes: [pc3-data:/data]
    stdin_open: true
    tty: true

networks:
  traffic-net:
    driver: bridge

volumes:
  pc2-data:
  pc3-data:
```

#### Step 6.2 - Dockerfiles
- [ ] Based on `python:3.11-slim`
- [ ] Install `pyzmq`
- [ ] Copy respective PC code + common modules
- [ ] Entrypoint scripts that launch all processes for that PC

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
                                                           PUB/PUSH ---------+-----> Semaphore Control (PC2)
                                                           REP <---REQ-------+-----> Monitoring (PC3)
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
