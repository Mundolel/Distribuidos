# Monitoring Fallback: Cross-PC Import Bug and Resolution

## Problem

The fallback monitoring CLI (`pc2/monitoring_fallback.py`) crashed with a
`ModuleNotFoundError` when run inside the PC2 Docker container:

```
$ docker exec -it pc2-analytics python -m pc2.monitoring_fallback
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "/app/pc2/monitoring_fallback.py", line 24, in <module>
    from pc3.monitoring_service import (
ModuleNotFoundError: No module named 'pc3'
```

### Root Cause

The fallback CLI was designed to reuse the menu, command handlers, and response
formatters from `pc3/monitoring_service.py` via a direct import:

```python
# pc2/monitoring_fallback.py (BROKEN)
from pc3.monitoring_service import (
    MENU, do_force_green_wave, do_force_semaphore,
    do_health_check, do_query_history,
    do_query_intersection, do_system_status,
)
```

This worked during local development where both `pc2/` and `pc3/` are on the
`PYTHONPATH`. However, in Docker, each PC has its own isolated image:

| Image | Contents |
|-------|----------|
| `distribuidos-pc1` | `common/` + `config/` + `pc1/` |
| `distribuidos-pc2` | `common/` + `config/` + `pc2/` |
| `distribuidos-pc3` | `common/` + `config/` + `pc3/` |

The `Dockerfile.pc2` only copies `common/`, `config/`, and `pc2/`:

```dockerfile
COPY common/ ./common/
COPY config/ ./config/
COPY pc2/ ./pc2/
```

There is no `COPY pc3/ ./pc3/` — and there shouldn't be. Each container
should only contain the code for its own PC. The `pc3/` module does not
exist inside the PC2 container, so the import fails at runtime.

### Why It Wasn't Caught Earlier

- **Local development**: All code is on the same `PYTHONPATH`, so the import
  resolves fine.
- **Unit tests**: `tests/test_monitoring.py` also imported from
  `pc3.monitoring_service` — this works in CI because all code is checked
  out together. The tests never run inside Docker containers.
- **Phase 6 integration tests**: These tested the core sensor-analytics-DB
  pipeline and the failover mechanism. The fallback CLI was not exercised
  via `docker exec` during those tests.

## Solution

### Approach: Extract Shared Code to `common/`

We created a new module `common/monitoring_commands.py` that contains all the
shared functions. Both `pc3/monitoring_service.py` and
`pc2/monitoring_fallback.py` now import from `common/` instead of having a
cross-PC dependency.

### What Was Moved to `common/monitoring_commands.py`

All reusable, non-PC-specific functions:

| Function | Purpose |
|----------|---------|
| `MENU` | The CLI menu string |
| `print_separator()` | Display helper |
| `print_response_header()` | Display helper |
| `format_intersection_response()` | Pretty-print query results |
| `format_history_response()` | Pretty-print history records |
| `format_green_wave_response()` | Pretty-print green wave activation |
| `format_semaphore_response()` | Pretty-print semaphore change |
| `format_system_status_response()` | Pretty-print system status |
| `format_health_check_response()` | Pretty-print health check result |
| `send_query()` | Send ZMQ REQ and receive REP response |
| `do_query_intersection()` | Interactive command: query intersection |
| `do_query_history()` | Interactive command: query history |
| `do_force_green_wave()` | Interactive command: force green wave |
| `do_force_semaphore()` | Interactive command: force semaphore |
| `do_system_status()` | Interactive command: system status |
| `do_health_check()` | Interactive command: health check |

### What Stayed in `pc3/monitoring_service.py`

PC3-specific code that should not be shared:

- Module-level logging configuration
- Signal handlers (`SIGINT`, `SIGTERM`)
- `_running` flag for graceful shutdown
- `REQ_TIMEOUT_MS` constant
- `run_monitoring_cli()` — the main loop (creates ZMQ socket, runs menu loop)
- `main()` entry point

### Files Changed

| File | Change |
|------|--------|
| `common/monitoring_commands.py` | **NEW** — extracted shared functions |
| `pc3/monitoring_service.py` | Removed extracted functions, now imports from `common.monitoring_commands` |
| `pc2/monitoring_fallback.py` | Changed `from pc3.monitoring_service` to `from common.monitoring_commands` |
| `tests/test_monitoring.py` | Changed `from pc3.monitoring_service` to `from common.monitoring_commands` for `format_*` imports |

### Why Not Just Copy `pc3/` Into the PC2 Image?

Adding `COPY pc3/ ./pc3/` to `Dockerfile.pc2` would have been a one-line fix.
We rejected this because:

1. **Architectural violation**: PC2 should not contain PC3's code. The Docker
   images mirror the physical machine separation in the architecture.
2. **Maintenance risk**: Changes to `pc3/monitoring_service.py` would silently
   affect the PC2 image even though PC2 shouldn't depend on it.
3. **Unnecessary bloat**: PC2 doesn't need `pc3/db_primary.py` or
   `pc3/start_pc3.py` — only the shared CLI functions.

Extracting to `common/` is the clean solution because `common/` is already
shared across all three PCs by design.

## Verification

### Unit Tests

All 167 tests pass (135 at the time of this refactoring, 167 after Phase 7 additions):

```
$ python -m pytest tests/ -v
167 passed
```

### Lint

```
$ python -m ruff check .
All checks passed!
```

### Docker Integration Test

```bash
# Build and start
docker compose build
docker compose up -d

# Verify the import works inside PC2 container
docker exec pc2-analytics python -c \
  "from common.monitoring_commands import MENU; print('Import OK')"
# Output: Import OK

# Verify the fallback CLI starts without error
docker exec pc2-analytics python -c \
  "from pc2.monitoring_fallback import run_fallback_cli; print('Fallback CLI import OK')"
# Output: Fallback CLI import OK

# Full failover test
docker stop pc3-monitoring
# Wait ~15s for health checker to detect failure
docker compose logs pc2 | findstr "FAILOVER"
# Output: [FAILOVER] PC3 is down. Using replica DB on PC2.

# Recovery test
docker start pc3-monitoring
# Wait ~10s for health checker to detect recovery
docker compose logs pc2 | findstr "RECOVERY"
# Output: [RECOVERY] PC3 is back. Resuming writes to primary DB.
```

## Lesson Learned

**Always test Docker `exec` commands during integration testing.** Code that
imports across PC boundaries (e.g., `pc2/` importing from `pc3/`) will work
in local development but fail in containerized deployments where each PC has
an isolated filesystem. Shared code belongs in `common/`.
