# Phase 3 Implementation Plan: PC2 - Analytics & Semaphore Control

## Overview

Phase 3 implements all PC2 components: the analytics service (core brain), semaphore
control service, and the replica database worker. These components receive sensor data
from PC1's broker, evaluate traffic rules, control semaphores, persist data, and respond
to monitoring queries from PC3.

---

## Design Decisions

### 1. DB Replica PUSH/PULL Topology
- **Pattern**: Replica binds PULL, Analytics connects PUSH
- `db_replica.py` binds PULL on `tcp://*:5564`
- `analytics_service.py` connects PUSH to `tcp://127.0.0.1:5564`
- Same pattern used for primary DB on PC3 (`db_primary.py` will bind PULL on `tcp://*:5563`)
- Idiomatic ZMQ: the stable "server" side (PULL worker) binds

### 2. Semaphore Direction Handling
- **Pattern**: Toggle whole intersection
- A command with `new_state="GREEN"` means: set the congested direction to GREEN, opposite to RED
- `traffic_light_control.py` manages the NS/EW toggle internally
- Analytics determines which direction based on sensor data context

### 3. DB Write Channel
- **Pattern**: Single PUSH/PULL per DB with JSON message envelope
- All data types (sensor events, congestion records, semaphore states, priority actions)
  go through the same PUSH/PULL channel per database
- Envelope format: `{"type": "<record_type>", "data": {...}}`

---

## ZMQ Socket Map for PC2

```
                    PC1                           PC2                           PC3
              ┌─────────────┐         ┌──────────────────────┐         ┌─────────────┐
              │ Broker PUB  │ ──SUB──>│  Analytics Service   │         │             │
              │ tcp://*:5560│         │                      │──PUSH──>│ DB Primary  │
              └─────────────┘         │  REP tcp://*:5561    │<──REQ── │ PULL *:5563 │
                                      │                      │         │             │
                                      │  PUSH───┐   PUSH────┤         │ Monitoring  │
                                      └─────────┼──────────┘         │ REQ         │
                                                │           │         └─────────────┘
                                      ┌─────────v─┐  ┌─────v────────┐
                                      │ Semaphore  │  │ DB Replica   │
                                      │ Control    │  │ PULL *:5564  │
                                      │ PULL *:5562│  └──────────────┘
                                      └────────────┘
```

### Socket Details

| Component            | Socket | Type | Address             | Direction |
|----------------------|--------|------|---------------------|-----------|
| analytics_service    | sensor_sub        | SUB  | tcp://pc1:5560       | connect   |
| analytics_service    | monitoring_rep    | REP  | tcp://*:5561         | bind      |
| analytics_service    | semaphore_push    | PUSH | tcp://127.0.0.1:5562 | connect   |
| analytics_service    | db_primary_push   | PUSH | tcp://pc3:5563       | connect   |
| analytics_service    | db_replica_push   | PUSH | tcp://127.0.0.1:5564 | connect   |
| traffic_light_control| command_pull      | PULL | tcp://*:5562         | bind      |
| db_replica           | data_pull         | PULL | tcp://*:5564         | bind      |

---

## Files to Create

### 1. `pc2/db_replica.py` (~120 lines)
- PULL socket binds on `tcp://*:5564`
- Receives JSON envelope from analytics
- Parses `type` field, calls appropriate `TrafficDB.insert_*()` method
- Logs operations to stdout

### 2. `pc2/traffic_light_control.py` (~180 lines)
- PULL socket binds on `tcp://*:5562`
- Initializes all 16 intersections with default state (NS=RED, EW=GREEN)
- Receives `SemaphoreCommand` JSON from analytics
- Updates in-memory state, logs every change
- Format: `[INT-B3] NS: RED->GREEN, EW: GREEN->RED (reason: congestion detected)`

### 3. `pc2/analytics_service.py` (~350 lines)
- Core brain of the system
- 5 ZMQ sockets (see table above)
- zmq.Poller monitors SUB + REP
- Per-intersection state tracking in memory
- Traffic rule evaluation (Normal / Congestion / Green Wave)
- Handles monitoring commands (query, force green wave, force semaphore, etc.)

### 4. `pc2/start_pc2.py` (~140 lines)
- Subprocess launcher (same pattern as start_pc1.py)
- Launch order: db_replica -> traffic_light_control -> analytics_service
- Delays between launches for socket binding
- Graceful shutdown on SIGINT/SIGTERM

---

## Traffic Rule Evaluation

### Per-Intersection State Tracking

```python
intersection_data = {
    "INT-A1": {
        "Q": 0,           # latest camera volumen (queue length)
        "Vp": 50.0,       # latest velocidad_promedio (camera or GPS)
        "D": 0,           # latest inductive vehiculos_contados (density proxy)
        "traffic_state": "NORMAL",
        "last_updated": "2026-..."
    }
}
```

### Rules (from config)

| State | Condition | Action |
|-------|-----------|--------|
| Normal | Q < 5 AND Vp > 35 AND D < 20 | Standard 15s cycle, no command |
| Congestion | Q >= 10 OR Vp <= 20 OR D >= 40 | Extend green +10s on congested direction |
| Green Wave | User command only | Force GREEN on all intersections in a row/column for 30s |

### Sensor Data Mapping

- **Camera** (`camara`): Updates `Q` (volumen) and `Vp` (velocidad_promedio)
- **GPS** (`gps`): Updates `Vp` (velocidad_promedio)
- **Inductive** (`espira`): Updates `D` (vehiculos_contados as density proxy)

---

## DB Envelope Format

All database writes use a JSON envelope sent over PUSH/PULL:

```json
{"type": "sensor_event", "data": {
    "sensor_id": "CAM-A1",
    "tipo_sensor": "camara",
    "interseccion": "INT-A1",
    "event_data": {...},
    "timestamp": "2026-..."
}}

{"type": "congestion_record", "data": {
    "interseccion": "INT-A1",
    "traffic_state": "CONGESTION",
    "decision": "EXTEND_GREEN",
    "details": "Q=12 >= 10",
    "sensor_data": {"Q": 12, "Vp": 18, "D": 5},
    "timestamp": "2026-..."
}}

{"type": "semaphore_state", "data": {
    "interseccion": "INT-A1",
    "state_ns": "GREEN",
    "state_ew": "RED",
    "reason": "congestion detected",
    "cycle_duration_sec": 25,
    "timestamp": "2026-..."
}}

{"type": "priority_action", "data": {
    "action_type": "GREEN_WAVE",
    "target": "row_B",
    "reason": "ambulance",
    "requested_by": "user",
    "affected_intersections": ["INT-B1","INT-B2","INT-B3","INT-B4"],
    "timestamp": "2026-..."
}}
```

---

## Monitoring Commands Handled by Analytics REP

| Command | Description | Required Fields |
|---------|-------------|-----------------|
| QUERY_INTERSECTION | Current state of an intersection | interseccion |
| QUERY_HISTORY | Congestion history in time range | timestamp_inicio, timestamp_fin |
| FORCE_GREEN_WAVE | Force green wave on a row/column | row or column, reason |
| FORCE_SEMAPHORE | Force specific semaphore change | interseccion, new_state |
| SYSTEM_STATUS | Overall system summary | (none) |

---

## Launch Order (start_pc2.py)

1. `db_replica.py` - binds PULL on :5564 (needs to be ready before analytics pushes)
2. `traffic_light_control.py` - binds PULL on :5562 (needs to be ready before analytics pushes)
3. Wait 1s for sockets to bind
4. `analytics_service.py` - connects to all sockets

---

## Testing Plan (tests/test_analytics.py)

- Test traffic rule evaluation: normal, congestion, edge cases
- Test sensor event parsing from ZMQ message format
- Test DB envelope creation and parsing
- Test MonitoringQuery handling for each command type
- ZMQ integration: sensor event -> analytics -> semaphore command chain
