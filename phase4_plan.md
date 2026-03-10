# Phase 4 Implementation Plan: PC3 - Primary DB & Monitoring

## Overview

Phase 4 implements all PC3 components: the primary database worker that persists
all data from the analytics service, and the interactive monitoring/query CLI
that allows users to inspect system state and issue direct commands (e.g., green
wave for ambulance passage).

---

## Design Decisions

### 1. Shared `process_envelope()` Function
- **Decision**: Duplicate the function in both `db_primary.py` and `db_replica.py`
- No refactoring to a shared location; keeps each file self-contained
- Both files handle the same 4 envelope types: `sensor_event`, `congestion_record`,
  `semaphore_state`, `priority_action`

### 2. Health Check REP Socket (Phase 5 Future-Proofing)
- **Decision**: Include in Phase 4 inside `db_primary.py`
- A daemon background thread binds REP on `tcp://*:5565` (`health_check_rep`)
- Responds to `"PING"` with `"PONG"`
- If `db_primary.py` is alive, PC3 is alive - ideal placement for heartbeat

### 3. Monitoring CLI Stdin Handling
- **Decision**: `start_pc3.py` launches `db_primary` as a background subprocess,
  then runs `monitoring_service.main()` in the foreground (main process)
- The foreground process gets stdin/stdout directly for the interactive menu
- Docker Compose already sets `stdin_open: true` and `tty: true` on PC3

---

## ZMQ Socket Map for PC3

```
                PC2                                    PC3
    ┌────────────────────────┐            ┌──────────────────────────┐
    │ Analytics Service      │            │                          │
    │   REP tcp://*:5561 ◄───┼── REQ ────┤  Monitoring Service      │
    │                        │            │  (interactive CLI)       │
    │   PUSH ────────────────┼── PULL ───►│  DB Primary              │
    │   (to db_primary_pull) │            │  PULL tcp://*:5563       │
    └────────────────────────┘            │                          │
                                          │  Health Check (thread)   │
    Phase 5: PC2 health checker ── REQ ──►│  REP tcp://*:5565        │
                                          └──────────────────────────┘
```

### Socket Details

| Component          | Socket | Type | Address            | Direction |
|--------------------|--------|------|--------------------|-----------|
| db_primary         | data_pull       | PULL | tcp://*:5563        | bind      |
| db_primary         | health_check    | REP  | tcp://*:5565        | bind (thread) |
| monitoring_service | analytics_req   | REQ  | tcp://pc2:5561      | connect   |

---

## Files to Create

### 1. `pc3/db_primary.py` (~220 lines)
- Mirror of `pc2/db_replica.py` with these changes:
  - Logger: `"DB-Primary"`
  - Env var: `PRIMARY_DB_PATH`
  - Default path: `/data/traffic_primary.db`
  - Port config key: `db_primary_pull` (5563)
- Additional: health check daemon thread on port 5565
- Duplicated `process_envelope()` function (same 4 envelope types)

### 2. `pc3/monitoring_service.py` (~350 lines)
- Interactive CLI with menu loop
- REQ socket connects to `tcp://pc2:5561`
- 6 monitoring commands + exit option
- Pretty-print formatters for each response type
- Timeout on recv (5s) to avoid hanging if PC2 is down

### 3. `pc3/start_pc3.py` (~100 lines)
- Launches `db_primary` as background subprocess
- Waits 1s for PULL socket to bind
- Runs `monitoring_service.main()` in foreground (gets stdin)
- On exit, terminates db_primary subprocess

---

## Monitoring CLI Menu

```
============================================================
       TRAFFIC MONITORING SYSTEM - Command Center
============================================================
  1. Query intersection status
  2. Query congestion history
  3. Force green wave (emergency)
  4. Force semaphore change
  5. System status
  6. Health check
  0. Exit
============================================================
Select option:
```

### Command Parameters

| # | Command | Prompts |
|---|---------|---------|
| 1 | QUERY_INTERSECTION | Intersection ID (e.g., INT-A1) |
| 2 | QUERY_HISTORY | Intersection (optional), Start time (optional), End time (optional) |
| 3 | FORCE_GREEN_WAVE | Row (A-D) or Column (1-4), Reason |
| 4 | FORCE_SEMAPHORE | Intersection ID, New state (GREEN/RED), Reason |
| 5 | SYSTEM_STATUS | (none) |
| 6 | HEALTH_CHECK | (none) |

---

## Response Pretty-Printing

### QUERY_INTERSECTION
```
--- Intersection INT-A1 ---
  Traffic State: NORMAL
  Queue Length (Q): 3
  Avg Speed (Vp): 42.5 km/h
  Density (D): 8
  Semaphore: NS=RED, EW=GREEN
  Last Updated: 2026-03-04T12:00:00Z
  Recent Events: 5
```

### QUERY_HISTORY
```
--- Congestion History (3 records) ---
  [2026-03-04T11:30:00Z] INT-B2 | CONGESTION | EXTEND_GREEN | Q=12, Vp=18, D=45
  [2026-03-04T11:25:00Z] INT-C3 | CONGESTION | EXTEND_GREEN | Q=15, Vp=12, D=5
  [2026-03-04T11:20:00Z] INT-B2 | NORMAL     | NO_ACTION    | Q=3, Vp=42, D=8
```

### SYSTEM_STATUS
```
--- System Status ---
  Total Sensor Events: 1520
  Congestion Detections: 87
  Green Waves: 3
  Semaphore Changes: 245
  Currently Congested: INT-B2, INT-C3
  Active Green Waves: (none)
  Total Intersections: 16
```

---

## Launch Order (start_pc3.py)

1. `db_primary.py` as subprocess (binds PULL :5563 + health REP :5565)
2. Wait 1s for sockets to bind
3. `monitoring_service.main()` in foreground (connects REQ to pc2:5561)

---

## Docker Compose Change

Add `depends_on` to PC3 so PC2 starts first:
```yaml
pc3:
    ...
    depends_on:
      - pc2
```

---

## Testing Plan (tests/test_monitoring.py)

- TestDBPrimary: process_envelope for all 4 types + unknown type
- TestMonitoringQueryBuilding: correct MonitoringQuery for each command
- TestResponseFormatting: pretty-print output for each response type
- TestHealthCheck: PING/PONG via ZMQ REP socket
- ZMQ Integration: REQ/REP round-trip with mock analytics
