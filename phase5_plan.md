# Phase 5 Implementation Plan: Fault Tolerance

## Overview

Phase 5 implements automatic fault detection and failover when PC3 goes down.
The analytics service on PC2 periodically health-checks PC3's `db_primary`
process. If the check fails after N retries, the system enters FAILOVER mode:
DB writes go only to the local replica, and a fallback monitoring CLI becomes
available on PC2 itself so the user can still query the system.

When PC3 comes back, the health checker detects recovery automatically and
resumes writes to the primary DB (no manual intervention needed).

---

## Project Spec Requirement

> "Fallas: En su implementacion deben considerar una posible falla del PC3.
> Si esto ocurre, todos los procesos deben comenzar a usar inmediatamente la
> replica de la base de datos que se encuentra en el PC2. La operacion sera
> transparente para el cliente; el sistema debe continuar operando de forma
> ininterrumpida."

From the rubric (Excelente):
> "Ante la falla del PC3 todos los procesos se reconectan con la replica.
> La falla se detecta de forma automatica usando un patron, como por ejemplo,
> el health check. Los estudiantes explican el o los patrones de resiliencia
> implementados."

---

## Design Decisions

### 1. Health Check Pattern

- **Pattern**: REQ/REP heartbeat (PING/PONG)
- **Initiator**: PC2 analytics service (REQ client)
- **Responder**: PC3 db_primary health check thread (REP server, port 5565)
- **Interval**: Every 5 seconds (`health_check_interval_sec`)
- **Timeout**: 2000ms per attempt (`health_check_timeout_ms`)
- **Max retries**: 3 consecutive failures before declaring FAILOVER
- **Socket option**: `zmq.RCVTIMEO` for non-blocking receive with timeout

### 2. Health Checker Runs as a Daemon Thread Inside Analytics

- Not a separate process -- runs as a daemon thread in the analytics service
- Communicates failover state via a thread-safe `FailoverState` object
  (uses `threading.Lock` to protect a boolean `pc3_alive` flag)
- The analytics main loop reads this flag each iteration to decide
  whether to push to the primary DB

### 3. PUSH Socket Management During Failover

- **Problem**: ZMQ PUSH to a dead peer silently buffers messages, consuming
  memory indefinitely (ZMQ_SNDHWM default is 1000, but messages pile up)
- **Solution**: On failover, disconnect and close the primary PUSH socket.
  On recovery, create a new PUSH socket and reconnect to PC3
- This prevents memory accumulation during extended outages

### 4. Fallback Monitoring CLI on PC2

- The normal monitoring CLI runs on PC3, so when PC3 dies the user loses it
- A fallback CLI runs on PC2 using the same `monitoring_service` code
- It connects to `tcp://127.0.0.1:5561` (analytics REP on localhost)
  instead of `tcp://pc2:5561`
- The analytics REP handler already uses the local replica DB for all
  queries, so monitoring works transparently during failover
- Accessible via: `docker exec -it pc2-analytics python -m pc2.monitoring_fallback`

### 5. Recovery is Automatic (No DB Resync)

- The health checker keeps pinging PC3 even during failover
- When PC3 responds again, the system auto-recovers:
  - Reconnects the primary PUSH socket
  - Resumes dual writes (primary + replica)
  - Logs `[RECOVERY] PC3 is back`
- **No DB resync**: data written during failover only exists in the replica.
  This is acceptable for the project scope. A full resync would require a
  separate reconciliation mechanism (out of scope).

### 6. REQ Socket Reset on Timeout

- ZMQ REQ sockets enter a broken state after a send without a matching
  receive (e.g., when the peer dies mid-request)
- On timeout, we must close and recreate the REQ socket before retrying
- This is a well-known ZMQ pattern called "Lazy Pirate"

---

## Architecture During Normal Operation

```
PC2                                     PC3
┌──────────────────────────┐           ┌──────────────────────────┐
│ Analytics Service        │           │                          │
│   PUSH ─────────────────────────────►│ DB Primary (PULL :5563)  │
│   PUSH ──► DB Replica    │           │                          │
│   REP :5561 ◄── REQ ────────────────│ Monitoring CLI           │
│                          │           │                          │
│ Health Checker (thread)  │           │ Health REP :5565         │
│   REQ ──── PING ────────────────────►│   (responds PONG)       │
└──────────────────────────┘           └──────────────────────────┘
```

## Architecture During Failover

```
PC2                                     PC3 (DOWN)
┌──────────────────────────┐           ┌──────────────────────────┐
│ Analytics Service        │           │                          │
│   PUSH ──X (disconnected)│     X─────│ DB Primary (DEAD)       │
│   PUSH ──► DB Replica ✓  │           │                          │
│   REP :5561              │     X─────│ Monitoring CLI (DEAD)    │
│                          │           │                          │
│ Health Checker (thread)  │           │ Health REP (DEAD)        │
│   REQ ──── PING ────X    │     X─────│   (no response)         │
│                          │           └──────────────────────────┘
│ Fallback Monitoring CLI  │
│   REQ ──► localhost:5561 │
└──────────────────────────┘
```

---

## Socket Map

| Component | Socket | Type | Address | Direction |
|-----------|--------|------|---------|-----------|
| health_checker | hc_req | REQ | tcp://pc3:5565 | connect |
| db_primary (PC3) | hc_rep | REP | tcp://*:5565 | bind |
| monitoring_fallback | req | REQ | tcp://127.0.0.1:5561 | connect |

---

## Files to Create / Modify

### New Files

| File | Lines | Purpose |
|------|-------|---------|
| `pc2/health_checker.py` | ~130 | Health check module with FailoverState + checker thread |
| `pc2/monitoring_fallback.py` | ~50 | Fallback monitoring CLI for PC2 |
| `tests/test_failover.py` | ~150 | Unit tests for health checker + failover logic |
| `phase5_plan.md` | this file | Design decisions |

### Modified Files

| File | Changes |
|------|---------|
| `pc2/analytics_service.py` | Import health_checker, start thread, check failover flag in push_to_dbs, handle socket reconnect |
| `pc2/start_pc2.py` | Log fallback CLI instructions on failover detection |

---

## Testing Plan (tests/test_failover.py)

- **TestFailoverState**: thread-safe state transitions (alive → failover → recovery)
- **TestHealthChecker**: PING/PONG success, timeout counting, failover trigger, recovery trigger
- **TestPushFailoverBehavior**: push_to_dbs skips primary when pc3_alive=False
- **TestLazyPirateReset**: REQ socket recreation after timeout
- **ZMQ Integration**: health check with mock REP that stops responding mid-test
