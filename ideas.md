# Post-Project Enhancement Ideas

> These ideas are **not part of the graded deliverable**. They are planned
> enhancements to be built after Phase 7 (Performance Testing) and Phase 8
> (Documentation) are complete. Both enhancements build on top of the existing
> architecture.
>
> The core project (PC1-PC3) must work standalone on the university VM without
> any of these enhancements. Docker Compose **profiles** are used to make
> enhancements opt-in.

---

## Docker Compose Profiles Strategy

Enhancements are toggled via Docker Compose profiles. Services without a
`profiles:` key always start. Services with profiles are opt-in:

```bash
# University VM — just the core project (always works)
docker compose up

# University VM — core + GUI dashboard
docker compose --profile gui up

# Your PC — core + GUI + ESP32 bridge
docker compose --profile gui --profile esp32 up
```

```yaml
# docker-compose.yml additions (do NOT modify existing pc1/pc2/pc3 services)
services:
  # ... existing pc1, pc2, pc3 unchanged ...

  pc4-gui:
    build: { context: ., dockerfile: Dockerfile.pc4 }
    container_name: pc4-gui
    profiles: ["gui"]                     # opt-in
    networks: [traffic-net]
    ports:
      - "8080:8080"                       # browser access

  pc5-esp32-bridge:
    build: { context: ., dockerfile: Dockerfile.esp32bridge }
    container_name: pc5-esp32-bridge
    profiles: ["esp32"]                   # opt-in
    networks: [traffic-net]
    ports:
      - "5570:5570"                       # ESP32 connects from external network
    depends_on:
      pc1: { condition: service_started }
```

What happens on the university VM when profiles are not activated:
- `pc4-gui` and `pc5-esp32-bridge` **do not exist** — Docker doesn't build,
  pull, or start them
- The ~20 lines of code changes for ESP32 topic support (in broker, constants,
  analytics) are **dormant** — no one publishes `"prediccion"` events, so the
  new SUB socket sits idle and the new `elif` branch never fires
- The system behaves identically to the original architecture

---

## Enhancement 1: Real-Time City Grid GUI (Browser-Based)

### Concept

A lightweight browser-based dashboard that displays a visual 4x4 city grid
with live traffic data, semaphore states, and congestion indicators. It does
NOT replace the existing CLI — the monitoring CLI could get a new command
(e.g., option `7. Open live dashboard`) that prints the URL, or the user
simply opens `http://localhost:8080` in a browser.

### Architecture

```
Browser (HTML/JS)  <──WebSocket──>  Python Bridge  <──ZMQ SUB──>  Broker PUB (pc1:5560)
   (user's browser)                 (pc4-gui)             │
                                                    ZMQ REQ ──>  Analytics REP (pc2:5561)
```

- The bridge process SUB-connects to the broker's PUB socket on port 5560
  (same as the analytics service). ZMQ PUB natively fans out to all
  connected subscribers — no changes needed to the broker.
- For on-demand queries (intersection state, system status), the bridge
  REQ-connects to the analytics REP on port 5561. The existing REP socket
  handles multiple REQ clients via fair-queuing (serialized). Under
  interactive load this works without modification.
- The browser receives data via WebSocket and renders a 4x4 grid with
  colored cells (green/red for semaphore state, intensity for congestion).

### Compatibility with Existing Code

**ZERO existing source files need modification.**

| Integration Point | Port | Existing Code | Impact |
|-------------------|------|---------------|--------|
| SUB to broker PUB | 5560 | `pc1/broker.py:70` binds PUB | None — PUB fans out natively |
| REQ to analytics REP | 5561 | `pc2/analytics_service.py:669` binds REP | None — REP handles multiple REQ clients |

The REP socket processes requests sequentially (recv-send lockstep). With two
REQ clients (PC3 monitoring CLI + GUI bridge), requests are fair-queued. Under
interactive/low-load usage (which is all this project needs) there is no
conflict. Under heavy load, the REP would need to be upgraded to a ROUTER
socket, but that is out of scope.

### Tech Stack

- **Backend**: Python `websockets` library (~60 lines). No web framework
  needed. No FastAPI, no Flask — just a WebSocket server that bridges ZMQ
  events to browser clients.
- **Frontend**: Single vanilla HTML file with inline CSS/JS. No build tools,
  no React, no npm. A 4x4 CSS grid with dynamically colored cells.
- **Docker**: New `Dockerfile.pc4` based on `python:3.11-slim`. Installs
  `pyzmq` and `websockets`. Joins `traffic-net`.

### New Files (no existing files touched)

```
pc4/
├── __init__.py
├── gui_bridge.py          # ZMQ SUB + WebSocket server (~60 lines)
└── static/
    └── index.html         # 4x4 grid dashboard (single file, ~200 lines)
Dockerfile.pc4             # python:3.11-slim + websockets + pyzmq
```

### What the Dashboard Shows

Each cell in the 4x4 grid represents one intersection (INT-A1 through INT-D4):
- Semaphore state: colored indicator (red/green for NS, opposite for EW)
- Queue length (Q): from latest camera event
- Average speed (Vp): from latest camera/GPS event
- Density (D): from latest inductive event
- Traffic state: NORMAL / CONGESTION / GREEN_WAVE (background color)

### Rough Implementation Outline

**`pc4/gui_bridge.py`:**
1. Create ZMQ SUB socket, connect to `tcp://pc1:5560`, subscribe to all topics
2. Create ZMQ REQ socket, connect to `tcp://pc2:5561`
3. Start WebSocket server on `0.0.0.0:8080`
4. Main loop: poll ZMQ SUB for sensor events, push them to all connected
   WebSocket clients as JSON
5. On WebSocket message from browser (e.g., "query INT-A1"): send REQ to
   analytics, forward REP response to browser

**`pc4/static/index.html`:**
1. Open WebSocket to `ws://localhost:8080/ws`
2. On message: parse JSON, update the corresponding grid cell
3. CSS grid: 4 columns, 4 rows, each cell shows intersection data
4. Color coding: green background for NORMAL, red for CONGESTION, blue for
   GREEN_WAVE

---

## Enhancement 2: ESP32 IoT Module — Traffic Prediction

### Concept

An ESP32 microcontroller connects to the system via a TCP bridge and runs a
simple linear regression model to predict traffic congestion 5 minutes ahead.
When predicted congestion exceeds thresholds, the ESP32 publishes a
`"prediccion"` event that flows through the normal sensor pipeline.

### Architecture

```
ESP32 (MicroPython/C)  ──plain TCP/JSON──>  Bridge Process  ──ZMQ PUB──>  Broker
       │                                     (pc5, on PC1)          │
       │                                                    Broker SUB ──> PUB ──> Analytics
       │
       └── receives sensor data from bridge (relayed from broker)
```

The bridge process runs in a separate container (`pc5-esp32-bridge`) on the
Docker bridge network. It:
1. Listens on a plain TCP socket (port 5570) for JSON from the ESP32
2. Publishes received prediction events via ZMQ PUB to port 5570 (broker
   SUBs to this port)
3. Optionally relays sensor data from the broker back to the ESP32 via the
   same TCP connection

**Why plain TCP instead of ZeroMQ on the ESP32:**
ZeroMQ uses the ZMTP wire protocol (greeting handshake, frame headers,
multipart message flags) on top of TCP. The ESP32 doesn't have native ZMQ
support — implementing ZMTP manually would be fragile and break on version
mismatches. A plain TCP socket sending JSON is trivial on ESP32 and robust.

### Network Connectivity: ESP32 to Docker

The Docker bridge network (`traffic-net`) is isolated inside the host machine.
The ESP32 on an external WiFi network (e.g., phone hotspot) cannot reach
Docker-internal IPs. The solution is **Docker port mapping**:

```yaml
pc5-esp32-bridge:
  ports:
    - "5570:5570"    # Maps host:5570 → container:5570
```

The ESP32 connects to `<laptop-IP>:5570`. The laptop's IP on the hotspot
network (e.g., `192.168.43.100`) is reachable from the ESP32.

```
Phone Hotspot (192.168.43.x)
  ├── Laptop (192.168.43.100)
  │     └── Docker
  │           └── pc5-esp32-bridge listening on :5570
  │                 ↕ port mapped to host:5570
  └── ESP32 (192.168.43.15)
        └── TCP connect to 192.168.43.100:5570   ✓ works
```

**IP discovery options:**
- **Static**: Hardcode laptop's hotspot IP in ESP32 config (hotspot IPs are
  usually stable, e.g., `192.168.43.1` or similar)
- **mDNS**: ESP32 (Arduino) supports mDNS — laptop advertises as
  `mylaptop.local`, ESP32 resolves it without knowing the numeric IP
- **Print at startup**: The bridge process prints the host's IP at launch

### Compatibility with Existing Code

**Requires minor modifications to 4 existing files (~20 lines total).**

All changes are **additive** (new list entries, new elif branches). No existing
logic is altered — the 3 original sensor types continue to work identically.
When the ESP32 bridge is not running (university VM), these changes are
completely dormant.

| File | Change | Lines Added |
|------|--------|-------------|
| `config/city_config.json` | Add `"sensor_esp32_pub": 5570` to `zmq_ports`, add `"prediccion": "prediccion"` to `zmq_topics` | +2 |
| `common/constants.py` | Add `TOPIC_PREDICTION = "prediccion"`, include it in `ALL_SENSOR_TOPICS` | +2 |
| `pc1/broker.py` | Add 4th entry to `sensor_ports` list (line 75-79): `("sensor_esp32_pub", "prediccion")` | +1 |
| `pc1/broker_threaded.py` | Same addition to `sensor_ports` list (line 164-168) | +1 |
| `pc2/analytics_service.py` | Add handler branch in `update_intersection_from_event()` (line 145-151) for `TOPIC_PREDICTION` | ~10 |

**Why these changes are safe on the university VM:**
- The broker creates a 4th SUB socket connecting to port 5570. If nothing
  is publishing there, the socket sits idle with zero CPU/memory overhead.
- The analytics `elif topic == TOPIC_PREDICTION:` branch never fires because
  no prediction events arrive through the pipeline.
- All 167 existing tests continue to pass — the new topic is simply unused.

### ML Algorithm: Linear Regression on Sliding Window

The ESP32 maintains a sliding window of the last 20 samples for one
intersection's key metrics (Q, Vp, D). For each metric, it computes a
simple linear regression:

```
slope = (N * sum(x*y) - sum(x) * sum(y)) / (N * sum(x^2) - sum(x)^2)
intercept = (sum(y) - slope * sum(x)) / N
predicted_value = slope * (current_time + 300) + intercept   # 5 min ahead
```

If predicted Q >= 10 OR predicted Vp <= 20 OR predicted D >= 40 (same
thresholds as the congestion rule), the ESP32 emits a prediction event.

### Prediction Event Format

```json
{
  "sensor_id": "ESP32-PRED-01",
  "tipo_sensor": "prediccion",
  "interseccion": "INT-B2",
  "predicted_state": "CONGESTION",
  "predicted_Q": 12.5,
  "predicted_Vp": 18.3,
  "predicted_D": 42.1,
  "prediction_horizon_sec": 300,
  "confidence": 0.85,
  "timestamp": "2026-03-14T10:30:00Z"
}
```

### New Files

```
pc5/
├── __init__.py
├── esp32_bridge.py        # TCP server + ZMQ PUB (~80 lines)
esp32/
├── main.py                # MicroPython: TCP client + linear regression (~120 lines)
└── config.json            # ESP32 config: server IP, port, intersection to monitor
Dockerfile.esp32bridge     # python:3.11-slim + pyzmq
```

### ESP32 Hardware Requirements

- Any ESP32 dev board (e.g., ESP32-WROOM-32, ESP32-S3)
- WiFi connection to the same network as the host machine (phone hotspot)
- MicroPython firmware (or Arduino/C++ with WiFiClient)
- No additional physical sensors needed — the ESP32 receives traffic data
  from the system and predicts based on that data

### Integration Flow

```
Normal flow (unchanged):
  Camera PUB:5555 ──> Broker SUB ──> Broker PUB:5560 ──> Analytics SUB

New prediction flow (additive):
  ESP32 ──TCP──> Bridge PUB:5570 ──> Broker SUB ──> Broker PUB:5560 ──> Analytics SUB
                                                                              │
                                                              "prediccion" topic handler
                                                              logs prediction, optionally
                                                              triggers preemptive semaphore
                                                              adjustment
```

---

## Summary: What Runs Where

```
University VM:                          Your PC (development):
  docker compose up                       docker compose --profile gui --profile esp32 up

  ┌──────────────┐                        ┌───────────────────┐
  │ pc1 (sensors)│                        │ pc1 (sensors)     │
  │ pc2 (analyt.)│                        │ pc2 (analytics)   │
  │ pc3 (monitor)│                        │ pc3 (monitoring)  │
  └──────────────┘                        │ pc4-gui (browser) │  ← --profile gui
                                          │ pc5-esp32-bridge  │  ← --profile esp32
                                          └────────┬──────────┘
                                                   │ port 5570 exposed
                                                   ▼
                                            ESP32 on phone hotspot
```

## Prerequisites

- Phase 7 (Performance Testing) complete
- Phase 8 (Documentation) complete
- All 167 tests passing
- Docker integration verified

Enhancement 1 (GUI) can be implemented independently of Enhancement 2 (ESP32).
Enhancement 2 requires an ESP32 dev board and a WiFi network shared with the
host machine.
