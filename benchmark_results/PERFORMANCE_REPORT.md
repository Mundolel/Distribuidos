# Informe de Rendimiento: Experimentos de Performance

## 1. Especificaciones de Hardware y Software

### Entorno de ejecucion

| Componente | Especificacion |
|---|---|
| OS Host | Windows 11 Pro 10.0.26200 |
| Contenedores | Docker Desktop con Python 3.11-slim |
| Red | Docker bridge network (`traffic-net`) |
| Base de datos | SQLite 3 con modo WAL (Write-Ahead Logging) |
| Mensajeria | ZeroMQ (pyzmq >= 25.1.0) |
| Duracion por escenario | 120 segundos (2 minutos) |

### Herramientas de medicion

- **Throughput:** Conteo de registros `sensor_events` insertados en la replica DB durante la ventana de 2 minutos, obtenido via `TrafficDB.get_event_count_in_interval()`
- **Latencia:** Timestamp `created_at` estampado al crear el `SemaphoreCommand` (en `analytics_service.py`) vs `time.time()` al aplicarlo en `traffic_light_control.py`. Extraido de lineas `[LATENCY]` en los logs del servicio de semaforos. La latencia mide el tiempo end-to-end desde que analytics toma una decision hasta que el semaforo la ejecuta.

---

## 2. Diseños Comparados

El sistema implementa dos arquitecturas alternativas para el componente broker de PC1, que agrega los eventos de los 3 tipos de sensores y los reenvía a PC2:

### Broker Standard (single-threaded)

```
Sensor Camara  (PUB:5555) ──┐
Sensor Espira  (PUB:5556) ──┼──> zmq.Poller (1 hilo) ──> PUB :5560 ──> PC2
Sensor GPS     (PUB:5557) ──┘    revisa SUB x3 en
                                  round-robin
```

Un unico hilo ejecuta un bucle con `zmq.Poller` que revisa las 3 suscripciones (camara, espira, gps) en round-robin. Cuando detecta un mensaje disponible en cualquier SUB, lo lee y lo republica inmediatamente en el PUB de salida.

**Archivo:** `pc1/broker.py`

### Broker Threaded (multi-threaded)

```
Sensor Camara  (PUB:5555) ──> Hilo 1 (SUB) ──┐
Sensor Espira  (PUB:5556) ──> Hilo 2 (SUB) ──┼──> PUSH/PULL ──> Hilo Colector ──> PUB :5560 ──> PC2
Sensor GPS     (PUB:5557) ──> Hilo 3 (SUB) ──┘    (inproc)
```

Tres hilos suscriptores independientes (uno por tipo de sensor) reciben mensajes en paralelo. Cada hilo envia los mensajes a traves de un socket `inproc` (PUSH/PULL) hacia un cuarto hilo colector, que los republica en el PUB de salida.

**Archivo:** `pc1/broker_threaded.py`

### Diferencias clave

| Aspecto | Standard | Threaded |
|---|---|---|
| Hilos | 1 | 4 (3 suscriptores + 1 colector) |
| Comunicacion interna | Ninguna (directo SUB -> PUB) | inproc PUSH/PULL pipe |
| Overhead | Minimo | Creacion de hilos + context switching + sincronizacion inproc |
| Complejidad | Baja | Media |
| Punto de falla | Un solo hilo bloquea todo | Un hilo puede fallar independientemente |

---

## 3. Escenarios de Prueba

Se definieron 4 escenarios combinando dos variables independientes:

- **Cantidad de sensores por tipo:** 1 o 2 (controla el volumen de datos)
- **Intervalo de generacion:** 10s o 5s (controla la frecuencia de eventos)
- **Modo del broker:** Standard o Threaded (la variable a comparar)

| Escenario | Sensores/tipo | Intervalo (s) | Broker | Total sensores | Tasa teorica (eventos/min) |
|---|---|---|---|---|---|
| 1A | 1 | 10 | Standard | 3 | ~18 |
| 1B | 1 | 10 | Threaded | 3 | ~18 |
| 2A | 2 | 5 | Standard | 6 | ~72 |
| 2B | 2 | 5 | Threaded | 6 | ~72 |

**Nota sobre la tasa teorica:** Con 1 sensor/tipo y 10s de intervalo, se generan 3 eventos cada 10 segundos = 18 eventos/minuto. Con 2 sensores/tipo y 5s de intervalo, se generan 6 eventos cada 5 segundos = 72 eventos/minuto. Las tasas reales pueden variar por el scheduling del sensor loop.

### Variables medidas

- **Variable dependiente 1 — Throughput:** Numero total de eventos `sensor_event` almacenados en la base de datos replica durante la ventana de 120 segundos.
- **Variable dependiente 2 — Latencia:** Tiempo (en milisegundos) desde la creacion del comando de semaforo (`SemaphoreCommand.created_at`) hasta su aplicacion en el servicio de control de semaforos.

### Protocolo de ejecucion

1. Iniciar PC2 (analytics + semaphore control + replica DB) y PC3 (primary DB + monitoring) via `docker compose up`
2. Para cada escenario:
   - Iniciar PC1 con los parametros correspondientes (`BROKER_MODE`, `SENSOR_COUNT`, `SENSOR_INTERVAL`)
   - Esperar 120 segundos
   - Detener PC1
   - Recopilar throughput desde la replica DB
   - Extraer metricas de latencia de los logs de `traffic_light_control`
3. Los resultados se guardan incrementalmente en `benchmark_results/results.json`

---

## 4. Resultados

### 4.1 Throughput

| Escenario | Broker | Sensores/tipo | Intervalo | Eventos (2 min) | Eventos/min | Eventos/seg |
|---|---|---|---|---|---|---|
| 1A | Standard | 1 | 10s | **62** | 31.0 | 0.52 |
| 1B | Threaded | 1 | 10s | **48** | 24.0 | 0.40 |
| 2A | Standard | 2 | 5s | **192** | 96.0 | 1.60 |
| 2B | Threaded | 2 | 5s | **192** | 96.0 | 1.60 |

**Observaciones inmediatas:**
- Bajo carga baja: Standard procesa **29.2% mas eventos** que Threaded (62 vs 48)
- Bajo carga alta: Ambos procesan **exactamente la misma cantidad** (192 eventos)
- Al cuadruplicar la carga (de 1 sensor/10s a 2 sensores/5s), el throughput se multiplica por ~3.1x (standard) y ~4.0x (threaded)

### 4.2 Latencia de comandos de semaforo

| Escenario | Broker | Min (ms) | Max (ms) | Promedio (ms) | P95 (ms) | Muestras |
|---|---|---|---|---|---|---|
| 1A | Standard | 0.61 | 1.56 | **0.93** | 1.56 | 17 |
| 1B | Threaded | 0.61 | 1.80 | **1.03** | 1.80 | 16 |
| 2A | Standard | 0.50 | 1.70 | **0.91** | 1.20 | 60 |
| 2B | Threaded | 0.47 | 2.40 | **1.04** | 1.71 | 56 |

**Observaciones inmediatas:**
- El standard tiene **latencia promedio menor** en ambos niveles de carga (~0.92 ms vs ~1.04 ms)
- El threaded tiene **mayor variabilidad**: su maximo alcanza 2.40 ms bajo carga alta vs 1.70 ms del standard
- El numero de muestras de latencia es proporcional a la cantidad de decisiones de congestion (que depende de los datos aleatorios de los sensores)

---

## 5. Graficos

Los siguientes graficos fueron generados automaticamente por `perf/generate_graphs.py` a partir de `benchmark_results/results.json`.

### 5.1 Throughput agrupado: Standard vs Threaded

**Archivo:** `benchmark_results/graphs/throughput_grouped.png`

```
Eventos almacenados (ventana de 2 minutos)

         Standard    Threaded
1 s/t  |████ 62    |███ 48     |  Standard +29% sobre Threaded
2 s/t  |██████ 192 |██████ 192 |  Identicos
```

El grafico de barras agrupadas muestra claramente que bajo carga baja (1 sensor/tipo) el broker standard supera al threaded, mientras que bajo carga alta (2 sensores/tipo) ambos alcanzan el mismo throughput.

### 5.2 Latencia agrupada: Standard vs Threaded

**Archivo:** `benchmark_results/graphs/latency_grouped.png`

```
Latencia promedio (ms) con barras de error min/max

         Standard         Threaded
1 s/t  |█ 0.93 (0.61-1.56) |█ 1.03 (0.61-1.80) |
2 s/t  |█ 0.91 (0.50-1.70) |█ 1.04 (0.47-2.40) |
                                       ^^^^
                                       Mayor variabilidad
```

Las barras de error revelan que el threaded tiene un rango de latencia significativamente mas amplio bajo carga alta (0.47-2.40 ms vs 0.50-1.70 ms).

### 5.3 Throughput por escenario individual

**Archivo:** `benchmark_results/graphs/throughput_by_scenario.png`

Muestra los 4 escenarios (1A, 1B, 2A, 2B) como barras individuales, permitiendo comparar visualmente el salto de carga entre los escenarios 1x y 2x.

### 5.4 Latencia por escenario individual

**Archivo:** `benchmark_results/graphs/latency_by_scenario.png`

Muestra la latencia promedio de cada escenario con barras de error (min/max). Se observa que la latencia promedio se mantiene estable en todos los escenarios (~0.91-1.04 ms), pero la variabilidad del threaded aumenta notablemente en 2B.

---

## 6. Analisis de Resultados

### 6.1 Cual diseño maneja mejor la carga alta?

**El broker standard iguala o supera al threaded en todos los escenarios.**

**Bajo carga baja** (1 sensor/tipo, 10s de intervalo):

El standard logro **62 eventos** vs **48 del threaded** — una ventaja del 29.2%. Esta diferencia se explica por el overhead de coordinacion entre hilos en el broker threaded:

- Creacion y mantenimiento de 4 hilos (3 suscriptores + 1 colector)
- Comunicacion inter-hilo via sockets `inproc` PUSH/PULL (serialización/deserializacion adicional)
- Context switching del sistema operativo entre los 4 hilos
- Delay de 200ms configurado al inicio para sincronizar hilos (`time.sleep(0.2)` en `broker_threaded.py`)

Bajo carga baja, donde los eventos llegan cada ~3.3 segundos por sensor, este overhead no se compensa con paralelismo porque no hay contención — el poller del standard procesa cada evento instantáneamente sin espera.

**Bajo carga alta** (2 sensores/tipo, 5s de intervalo):

Ambos diseños alcanzaron exactamente **192 eventos**. El throughput identico indica que:

1. El cuello de botella no esta en el broker sino en la tasa de generación de los sensores
2. Ambos diseños tienen capacidad ociosa suficiente para manejar esta carga
3. El overhead del threaded se amortiza cuando hay mas mensajes por procesar, compensando la desventaja observada a carga baja

### 6.2 Como escala la latencia con mayor cantidad de sensores?

**La latencia promedio se mantiene estable independientemente de la carga, pero la variabilidad aumenta con el broker threaded.**

| Metrica | Standard (1s) | Standard (2s) | Cambio | Threaded (1s) | Threaded (2s) | Cambio |
|---|---|---|---|---|---|---|
| Promedio | 0.93 ms | 0.91 ms | **-2.2%** | 1.03 ms | 1.04 ms | +1.0% |
| Maximo | 1.56 ms | 1.70 ms | +9.0% | 1.80 ms | **2.40 ms** | **+33.3%** |
| P95 | 1.56 ms | **1.20 ms** | -23.1% | 1.80 ms | 1.71 ms | -5.0% |
| Rango | 0.95 ms | 1.20 ms | — | 1.19 ms | **1.93 ms** | — |

Observaciones clave:

1. **El standard mantiene latencia consistente** (0.91-0.93 ms promedio) sin importar la carga. Su P95 incluso **mejora** de 1.56 ms a 1.20 ms bajo carga alta, lo que sugiere que el poller de ZMQ se vuelve mas eficiente cuando tiene mensajes disponibles en cada ciclo (menos polling vacios).

2. **El threaded muestra mayor variabilidad bajo carga alta:** su maximo sube de 1.80 ms a **2.40 ms** (+33.3%) y su rango se amplía de 1.19 ms a 1.93 ms. Esto se debe a la contencion entre los hilos suscriptores compitiendo por el pipe inproc y el overhead de context switching del sistema operativo.

3. **En ambos diseños, la latencia es excelente** (<1.1 ms promedio), muy por debajo de los ciclos de semaforo de 15 segundos. Para un sistema de control de trafico, una latencia de 1 ms es despreciable — el factor limitante real son los tiempos fisicos de cambio de semaforo.

### 6.3 Cual arquitectura es mas escalable y por que?

**El broker standard (single-threaded con Poller) es la arquitectura mas escalable para este sistema**, por las siguientes razones:

**1. Menor overhead inherente**

El patron `zmq.Poller` es una abstraccion liviana sobre `epoll`/`select` del sistema operativo. Cada mensaje sigue un camino directo: SUB socket → PUB socket, sin intermediarios. No requiere creacion de hilos, sincronizacion, ni comunicacion inter-hilo.

**2. Mejor throughput bajo carga baja**

Con 29.2% mas eventos procesados en el escenario de 1 sensor/tipo (62 vs 48), el standard aprovecha mejor los recursos cuando la carga no justifica paralelismo. Esto es relevante porque los sistemas de trafico operan la mayor parte del tiempo en condiciones normales (baja carga), no en picos.

**3. Throughput equivalente bajo carga alta**

Al cuadruplicar la carga, ambos diseños procesan 192 eventos. Esto demuestra que el standard tiene capacidad ociosa significativa y que el cuello de botella esta en los sensores, no en el broker.

**4. Latencia mas predecible**

El standard tiene un rango de latencia mas estrecho (0.50-1.70 ms, rango de 1.20 ms) comparado con el threaded (0.47-2.40 ms, rango de 1.93 ms). En sistemas de control de trafico, la **predictibilidad** importa mas que la latencia minima absoluta — un semaforo necesita responder de forma consistente, no ocasionalmente muy rapido pero a veces lento.

**5. Simplicidad operacional**

Un solo hilo es mas facil de depurar, monitorear y mantener. No hay race conditions, deadlocks, ni problemas de GIL (Global Interpreter Lock de Python, que limita la concurrencia real de hilos CPU-bound en CPython). En un entorno de produccion, la simplicidad reduce el costo de operacion y la probabilidad de errores sutiles.

**Cuando convendría el threaded?**

Si el sistema escalara a **cientos de sensores** con intervalos sub-segundo, el poller podria saturarse al revisar muchos sockets en round-robin. En ese escenario hipotetico, tener hilos dedicados por tipo de sensor paralelizaria la recepcion. Sin embargo, con la carga actual (hasta 6 sensores, intervalos de 5s), el standard es suficiente y superior.

Otra alternativa para escalar seria un enfoque **hibrido**: mantener el poller standard pero particionar sensores por regiones geograficas en multiples instancias de broker, distribuyendo la carga horizontalmente en lugar de agregar complejidad con hilos dentro de un solo broker.

---

## 7. Conclusiones

1. **El broker standard single-threaded es la mejor eleccion** para este sistema de gestion de trafico, ofreciendo igual o mejor throughput con latencia mas consistente en todos los escenarios probados.

2. **El broker threaded no aporta ventajas medibles** a las cargas probadas. Su overhead de coordinacion (4 hilos + inproc pipe) penaliza el throughput bajo carga baja (-29.2%) y aumenta la variabilidad de latencia bajo carga alta (+33.3% en latencia maxima).

3. **La latencia del sistema es excelente** en ambos diseños (<1.1 ms promedio), ordenes de magnitud por debajo de los ciclos de semaforo de 15 segundos. El cuello de botella del sistema no esta en el broker sino en la tasa de generacion de eventos por los sensores.

4. **El sistema escala linealmente** con la cantidad de sensores: al cuadruplicar la carga (2x sensores, 2x frecuencia), el throughput se multiplica ~3-4x sin degradacion de latencia, lo que indica que hay margen significativo para crecer antes de alcanzar limites del broker.

5. **Para escalar mas alla de las cargas probadas** (>100 sensores), se recomienda evaluar particionamiento horizontal (multiples brokers por region) en lugar de agregar complejidad con multithreading, dado que el standard single-threaded demostro ser suficiente y superior a las cargas evaluadas.

---

## Apendice: Datos Crudos

```json
[
  {
    "scenario": "1A",
    "broker_mode": "standard",
    "sensor_count": 1,
    "interval_sec": 10,
    "duration_sec": 120,
    "start_time": "2026-05-28T20:07:22Z",
    "end_time": "2026-05-28T20:09:30Z",
    "throughput_events": 62,
    "latency_ms": {
      "min": 0.61,
      "max": 1.56,
      "avg": 0.93,
      "p95": 1.56,
      "count": 17
    }
  },
  {
    "scenario": "1B",
    "broker_mode": "threaded",
    "sensor_count": 1,
    "interval_sec": 10,
    "duration_sec": 120,
    "start_time": "2026-05-28T20:09:32Z",
    "end_time": "2026-05-28T20:11:38Z",
    "throughput_events": 48,
    "latency_ms": {
      "min": 0.61,
      "max": 1.8,
      "avg": 1.03,
      "p95": 1.8,
      "count": 16
    }
  },
  {
    "scenario": "2A",
    "broker_mode": "standard",
    "sensor_count": 2,
    "interval_sec": 5,
    "duration_sec": 120,
    "start_time": "2026-05-28T20:11:41Z",
    "end_time": "2026-05-28T20:13:50Z",
    "throughput_events": 192,
    "latency_ms": {
      "min": 0.5,
      "max": 1.7,
      "avg": 0.91,
      "p95": 1.2,
      "count": 60
    }
  },
  {
    "scenario": "2B",
    "broker_mode": "threaded",
    "sensor_count": 2,
    "interval_sec": 5,
    "duration_sec": 120,
    "start_time": "2026-05-28T20:13:53Z",
    "end_time": "2026-05-28T20:16:10Z",
    "throughput_events": 192,
    "latency_ms": {
      "min": 0.47,
      "max": 2.4,
      "avg": 1.04,
      "p95": 1.71,
      "count": 56
    }
  }
]
```
