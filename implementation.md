# Robot Manager — Implementation Specification for Claude Code

## Context and Goal
We are building a highly modular "Robot Manager" for a distributed geospatial data storage system running on edge hardware (Intel NUC / NVIDIA Orin). The system acts as a peer-to-peer data mesh. It ingests high-throughput sensor data (~32 MB/s from RGB photo, RGB video, thermal, multispectral, LiDAR) from C++ hardware drivers, saves the binaries to ReductStore, saves the metadata to SpatiaLite, and answers distributed geospatial queries via Zenoh.

The system must support heterogeneous robots — UAVs and UGVs with different sensor configurations. A UAV might carry RGB + thermal + LiDAR, while a UGV might carry only RGB + multispectral. The software must handle this without code changes.

## Requirements Traceability
This implementation satisfies the following requirements (see requirements document for full descriptions):

| Requirement | How Addressed |
|---|---|
| F-01 (Ingest all sensor types incl. video) | Sensor registry + HAL with discrete-frame and video-chunk modes |
| F-02 (Metadata + spatial index) | SpatiaLite with R-tree spatial index |
| F-03 (P2P discovery, no broker) | Zenoh peer mode with UDP multicast scouting |
| F-04 (Parse and execute spatial queries) | NetworkWorker queryable + SpatiaLite SQL |
| F-05 (Aggregate and return results) | NetworkWorker assembles metadata + binary response |
| F-06 (FIFO eviction) | ReductStore bucket quota configuration on init |
| NF-01 (≥40 MB/s writes, non-blocking queries) | Async architecture, WAL mode, batched writes |
| NF-02 (Partition tolerance) | Local-only storage, no consensus required |
| NF-03 (Graceful reconnection) | Zenoh automatic scouting/rejoin |
| NF-04 (<2 GB RAM) | Embedded DBs, no server processes |
| D-01 (WGS84 EPSG:4326) | Enforced in SpatiaLite schema and validated on ingest |
| D-02 (Trajectory overlap support) | Scatter-gather query, each drone answers independently |
| D-03 (Heterogeneous robots) | Sensor registry pattern, robot-type config |

## Architectural Rules (STRICT CONCURRENCY MODEL)
To avoid Python GIL bottlenecks and IPC serialization overhead with large binary files:

1. **Core Loop:** The Python system runs on a single `asyncio` event loop.
2. **Message Bus:** Communication between modules happens exclusively via `asyncio.Queue`.
3. **C++ Hardware Layer:** Sensors are polled via a C++ extension built with `nanobind` (or `pybind11`). The C++ code MUST release the Python GIL (`gil_scoped_release`) while waiting for hardware/mock data.
4. **Blocking I/O Offload:** When Python calls the C++ sensor bindings or the SQLite database, it MUST use `asyncio.to_thread()` so it does not block the main async loop.
5. **Consumer-Side Batching:** The Storage worker must use a time-or-count buffer to batch ReductStore API calls and SQLite inserts to prevent disk thrashing.
6. **Modularity:** Every Python component must inherit from a common `BaseService` class with async `start()` and `stop()` methods.

## Coding Standards (ENFORCE STRICTLY)

1. **DRY (Don't Repeat Yourself):** Extract shared logic into helper functions or base classes. Never duplicate SQL queries, serialization logic, or validation code across modules.
2. **KISS (Keep It Simple Stupid):** Prefer the simplest solution that works. No premature abstractions, no unnecessary design patterns, no over-engineering. If a function does one thing, it should be short and obvious.
3. **Type hints everywhere:** Every function signature, every variable where the type is not immediately obvious. Use `from __future__ import annotations` in every file. Use `typing` generics where needed.
4. **Dataclasses, not dicts:** All structured data passed between components must be defined as `@dataclass` (or `@dataclass(frozen=True)` for immutable messages). Never use raw `dict` as a data holder. Define dataclasses in the module that owns the concept, import them where needed.
5. **Docstrings:** Brief, one-line explanation of what the function does. No `Args:`, no `Returns:`, no `Raises:` sections — the type hints already communicate that. Example:
   ```python
   async def insert_batch(self, records: list[SensorRecord]) -> None:
       """Bulk-insert sensor metadata records into SpatiaLite."""
   ```
6. **Naming:** snake_case for functions/variables, PascalCase for classes/dataclasses, UPPER_SNAKE for constants.

## Query Routing: Broadcast / Scatter-Gather (Option 2)

All drones register a Zenoh queryable on a **stable, fixed key expression**: `robot/*/query/spatial`. Every spatial query from any client is delivered to every online robot. Each robot filters locally against its own SpatiaLite index and responds only if it has matching data.

**Why broadcast over spatial-key routing:**
- **Historical queries work correctly.** A drone that flew over location X an hour ago still has that data in SpatiaLite and will answer. Spatial-key routing (where drones register/unregister by current H3 cell) would miss this — the drone has moved and is registered under a different cell.
- **No re-registration race conditions.** Spatial-key routing requires drones to continuously update their Zenoh key as they move, creating windows where a query can miss a drone mid-transition.
- **Simplicity.** No coordination logic, no cell computation on the drone side for routing purposes. Each drone just answers what it has.
- **Acceptable overhead at target fleet size.** For 5–20 robots, every robot receiving every query is negligible. Each local SpatiaLite R-tree lookup is sub-millisecond; the cost is almost entirely network fan-out, which Zenoh handles efficiently.

**Scaling note (future work):** For fleets of 100+ robots, a two-tier approach could be introduced: drones advertise the coarse H3 bounding region of their stored data, and clients use this to send targeted queries. This does not affect the current design.

### Query Flow (Detailed)

1. Client constructs a JSON payload: `{"bbox": [lon_min, lat_min, lon_max, lat_max], "time_start": "...", "time_end": "...", "sensor": "RGB"}`.
2. Client sends `zenoh.get("robot/*/query/spatial", payload)` with a configurable timeout (e.g., 3 seconds).
3. Zenoh delivers the query to every robot that has a registered queryable matching `robot/*/query/spatial`.
4. On each robot, the NetworkWorker callback fires:
   - Parses the JSON payload.
   - Translates to a SpatiaLite SQL query using R-tree spatial index.
   - If results exist: fetches binary data from ReductStore, assembles response.
   - If no results: returns empty response (or stays silent, configurable).
5. Client collects all responses within the timeout window and presents aggregated results.

## Sensor Data Model: Discrete Frames vs. Video Chunks

The system handles two fundamentally different data patterns:

### Discrete-frame sensors (photo, thermal, multispectral, LiDAR)
Each capture is a standalone blob (a JPEG, TIFF, LAZ chunk, etc.) with its own timestamp and GPS coordinate. The sensor worker polls the C++ HAL, receives one complete frame, and pushes it to the storage queue as a single message.

### Video streams (RGB video, potentially thermal video)
Video is a continuous H.264/H.265 encoded bitstream. It cannot be treated as individual frames without transcoding (which is too expensive on-drone at full rate). Instead, the system handles video as **fixed-duration chunks** (e.g., 2-second GOP-aligned segments).

**How video chunking works:**
1. The C++ HAL for a video sensor accumulates encoded NAL units from the hardware encoder.
2. Every N seconds (configurable, default 2s), or at every keyframe/IDR boundary, the HAL emits a complete chunk as a byte vector — a self-contained, decodable segment of the H.264/H.265 stream.
3. The Python sensor worker receives this chunk exactly like a discrete frame: a blob + metadata.
4. The metadata for a video chunk differs slightly: it has `time_start` and `time_end` (the temporal extent of the chunk) rather than a single `timestamp`, and the `geometry` represents the drone's position at the midpoint (or a linestring of positions during the chunk, if available).
5. Storage and querying proceed identically — the chunk is a blob in ReductStore, metadata goes to SpatiaLite.

**Why this works:** The drone's onboard hardware encoder (NVIDIA NVENC on Orin, Intel Quick Sync on NUC) produces the H.264/H.265 stream. We do not decode or re-encode. We simply segment the already-encoded stream at GOP boundaries and store the segments as blobs. This adds near-zero CPU overhead.

## Sensor Registry Pattern (D-03 Heterogeneity)

Different robots carry different sensor suites. The system must not hardcode which sensors exist. Instead, sensors are declared in a per-robot configuration file (`robot_config.yaml` at project root). A UGV config might only list `rgb_photo` and `multispectral`. The sensor worker reads this config and instantiates one C++ HAL driver per entry. No code changes needed.

Example `robot_config.yaml`:
```yaml
robot_id: "uav_01"
robot_type: "uav"

sensors:
  - name: "main_rgb"
    type: "rgb_photo"          # discrete frame
    driver: "MockFrameSensor"  # C++ class name in HAL
    params:
      resolution: [6000, 4000]
      fps: 1
    reductstore_bucket: "rgb_photo"

  - name: "rgb_video"
    type: "rgb_video"          # video chunk
    driver: "MockVideoSensor"
    params:
      resolution: [3840, 2160]
      fps: 30
      chunk_duration_sec: 2
    reductstore_bucket: "rgb_video"

  - name: "thermal"
    type: "thermal"
    driver: "MockFrameSensor"
    params:
      resolution: [640, 512]
      fps: 5
    reductstore_bucket: "thermal"

  - name: "lidar"
    type: "lidar"
    driver: "MockFrameSensor"
    params:
      points_per_sec: 300000
      chunk_duration_sec: 1
    reductstore_bucket: "lidar"
```

### HAL driver registry (C++ side)
The C++ binding exposes a factory function:
```cpp
std::unique_ptr<ISensor> create_sensor(const std::string& driver_name, /* params */);
```
Python calls this once per config entry. The returned sensor object is either a `IFrameSensor` (returns discrete blobs) or `IVideoSensor` (returns time-bounded chunks). Both expose `get_latest_data()` but the metadata shape differs.

## Tech Stack
* Language: Python 3.10+ and C++17
* Package manager: `uv` (all dependency management, virtual env, and script running via `uv`)
* Concurrency: `asyncio`
* C++ Bindings: `nanobind` (or `pybind11`)
* Communication: `eclipse-zenoh` (Zenoh Python API)
* Metadata/Spatial Index: `sqlite3` with `mod_spatialite` extension
* Blob Storage: `reduct-py` (ReductStore Python Client)
* Config: `pyyaml`

## Project Structure
```
robot_manager/
├── pyproject.toml             # uv project config, dependencies
├── main.py
├── robot_config.yaml
├── core/
│   ├── bus.py
│   ├── base_service.py
│   ├── config.py              # Loads and validates robot_config.yaml
│   └── models.py              # Shared dataclasses (SensorSample, StoredRecord, SpatialQuery, QueryResult)
├── cpp_sensors/
│   ├── CMakeLists.txt
│   ├── sensor_interface.hpp   # ISensor, IFrameSensor, IVideoSensor
│   ├── mock_frame_sensor.cpp  # MockFrameSensor
│   ├── mock_video_sensor.cpp  # MockVideoSensor
│   ├── sensor_factory.cpp     # create_sensor() factory
│   └── bindings.cpp           # nanobind Python bindings
├── services/
│   ├── sensor_worker.py       # Spawns one async task per configured sensor
│   ├── storage_worker.py
│   └── network_worker.py
└── database/
    └── db_client.py
```

---

## Phase 1 Prompts (Execute Sequentially)

### Prompt 0: Project Scaffolding with uv

"Initialize the project using `uv`:
1. Run `uv init robot_manager` to create the project.
2. Add Python dependencies: `uv add eclipse-zenoh reduct-py pyyaml`.
3. Add dev dependencies: `uv add --dev pytest pytest-asyncio`.
4. Create the directory structure: `core/`, `services/`, `database/`, `cpp_sensors/`, `tests/`. Add `__init__.py` files to `core/`, `services/`, `database/`.
5. Create an empty `robot_config.yaml` placeholder.

All subsequent commands should be run via `uv run` (e.g., `uv run python main.py`, `uv run pytest`)."

### Prompt 1: Core Framework, Config Loader, Data Models & Database Wrapper

"Create `core/base_service.py` defining an abstract `BaseService` with async `start()` and `stop()` methods and an `is_running` boolean flag.

Create `core/bus.py` containing a class `EventBus` that initializes an `asyncio.Queue` named `storage_queue`.

Create `core/models.py` defining the shared dataclasses used across all modules:
```python
@dataclass
class SensorSample:
    """A single captured blob (frame or video chunk) ready for storage."""
    data: bytes
    timestamp: datetime
    time_end: datetime | None  # None for discrete frames, set for video chunks
    sensor_type: str
    sensor_name: str
    lat: float
    lon: float
    altitude: float
    bucket: str

@dataclass
class StoredRecord:
    """Metadata of a record already persisted in SpatiaLite."""
    id: int
    timestamp: datetime
    time_end: datetime | None
    sensor_type: str
    sensor_name: str
    robot_id: str
    file_id: str
    bucket: str
    altitude: float
    heading: float
    pitch: float
    roll: float
    lat: float
    lon: float

@dataclass
class SpatialQuery:
    """Parameters for a spatial bounding-box query."""
    bbox: tuple[float, float, float, float]  # (lon_min, lat_min, lon_max, lat_max)
    time_start: datetime
    time_end: datetime
    sensor_type: str | None = None

@dataclass
class QueryResult:
    """A single result entry returned to a querying client."""
    metadata: StoredRecord
    data_b64: str
```

Create `core/config.py` that loads `robot_config.yaml` using PyYAML. Parse into dataclasses:
```python
@dataclass
class SensorConfig:
    name: str
    type: str          # one of 'rgb_photo', 'rgb_video', 'thermal', 'multispectral', 'lidar'
    driver: str
    params: dict[str, Any]
    reductstore_bucket: str

@dataclass
class RobotConfig:
    robot_id: str
    robot_type: str
    sensors: list[SensorConfig]
```
Validate that `robot_id` is non-empty and `sensors` is non-empty. Raise `ValueError` on invalid config.

Create `database/db_client.py`. Use the standard `sqlite3` library to connect to `robot_local.gpkg` and load the `mod_spatialite` extension.
1. **CRITICAL:** Upon connecting, execute `PRAGMA journal_mode=WAL;` and `PRAGMA synchronous=NORMAL;`.
2. Create table `sensor_metadata` with columns: `id` INTEGER PRIMARY KEY AUTOINCREMENT, `timestamp` DATETIME NOT NULL, `time_end` DATETIME (nullable, used for video chunks), `sensor_type` TEXT NOT NULL, `sensor_name` TEXT NOT NULL, `robot_id` TEXT NOT NULL, `file_id` TEXT NOT NULL, `bucket` TEXT NOT NULL, `altitude` REAL, `heading` REAL, `pitch` REAL, `roll` REAL, and `geometry` POINT using SRID 4326. Create a spatial index on the geometry column. Create a regular index on `timestamp`. Create a regular index on `sensor_type`.
3. **D-01 Validation:** Before inserting, validate that longitude is in [-180, 180] and latitude is in [-90, 90]. Reject records that fail this check with a logged warning.
4. Write a synchronous method `_insert_batch_sync(records: list[SensorSample], robot_id: str)` that uses a transaction (`BEGIN TRANSACTION` / `COMMIT`) and `executemany` with `MakePoint(lon, lat, 4326)`.
5. Write an async wrapper `insert_batch(records: list[SensorSample], robot_id: str) -> None` using `asyncio.to_thread()`.
6. Write a synchronous method `_query_spatial_sync(query: SpatialQuery) -> list[StoredRecord]` that queries using the R-tree spatial index (`MbrWithin` or `MbrIntersects` with `BuildMbr`). If `sensor_type` is provided, filter by it. Return list of `StoredRecord` dataclasses.
7. Write an async wrapper `query_spatial(query: SpatialQuery) -> list[StoredRecord]` using `asyncio.to_thread()`."

### Prompt 2: The Storage Consumer (Time-or-Count Batching + FIFO Quota)

"Create `services/storage_worker.py` inheriting from `BaseService`. It requires the `EventBus`, `db_client`, and `RobotConfig` on initialization. In `start()`, launch an asyncio background task that acts as a consumer-side batcher.

**ReductStore bucket initialization (F-06):** On startup, for each unique `reductstore_bucket` in the robot config, create the bucket in ReductStore if it doesn't exist. **Set a FIFO quota** on each bucket (e.g., 10 GB configurable per bucket). This ensures automatic eviction of oldest data when disk fills up.

Initialize `batch_buffer: list[SensorSample] = []`, `MAX_BATCH_SIZE = 50`, `FLUSH_INTERVAL_SEC = 0.5`. Track `last_flush_time`.

Inside the continuous async loop:
1. Attempt to pop a `SensorSample` from `EventBus.storage_queue` using `asyncio.wait_for(queue.get(), timeout=...)`. Calculate timeout dynamically as time remaining until next flush.
2. If item received, append to `batch_buffer`.
3. Check flush conditions: `len(batch_buffer) >= MAX_BATCH_SIZE` OR `TimeoutError` OR elapsed time exceeds `FLUSH_INTERVAL_SEC`:
   a. If buffer empty, reset timer and continue.
   b. For each `SensorSample` in buffer: write `sample.data` to ReductStore to `sample.bucket` using `sample.timestamp` as the ReductStore record timestamp.
   c. Collect the ReductStore record references (file IDs).
   d. Call `db_client.insert_batch(batch_buffer, config.robot_id)` to write all metadata to SpatiaLite.
   e. Clear buffer, reset timer.
4. Log exceptions during flush, use `queue.task_done()`."

### Prompt 3: C++ HAL — Sensor Interfaces, Mocks & Factory

"Create the C++ sensor abstraction layer in `cpp_sensors/`.

**`sensor_interface.hpp`:** Define:
- `struct FrameData { std::vector<uint8_t> data; std::string sensor_type; std::string sensor_name; double latitude; double longitude; double altitude; }` — used for discrete-frame sensors.
- `struct VideoChunkData { std::vector<uint8_t> data; std::string sensor_type; std::string sensor_name; double latitude; double longitude; double altitude; double time_start; double time_end; }` — used for video-chunk sensors.
- `class IFrameSensor` with `virtual FrameData get_latest_frame() = 0;`
- `class IVideoSensor` with `virtual VideoChunkData get_latest_chunk() = 0;`

**`mock_frame_sensor.cpp`:** Implement `MockFrameSensor : IFrameSensor`. Constructor takes `sensor_type`, `sensor_name`, `data_size_bytes` (e.g., 2MB for RGB, 0.65MB for thermal), and `interval_ms`. `get_latest_frame()` sleeps for `interval_ms` (simulating hardware wait), generates a dummy byte vector of `data_size_bytes`, and returns FrameData with mock GPS coordinates (randomized slightly around a Prague center point 50.08°N, 14.42°E to simulate movement).

**`mock_video_sensor.cpp`:** Implement `MockVideoSensor : IVideoSensor`. Constructor takes `sensor_type`, `sensor_name`, `chunk_size_bytes` (e.g., 15MB for a 2s 4K H.265 chunk), and `chunk_duration_sec`. `get_latest_chunk()` sleeps for `chunk_duration_sec * 1000` ms, generates a dummy byte vector, returns VideoChunkData with `time_start` and `time_end` spanning the chunk duration. Mock GPS as above.

**`sensor_factory.cpp`:** Implement `create_frame_sensor(name, type, params) -> unique_ptr<IFrameSensor>` and `create_video_sensor(name, type, params) -> unique_ptr<IVideoSensor>`. Map driver names ('MockFrameSensor', 'MockVideoSensor') to constructors.

**`bindings.cpp`:** Bind all of the above with nanobind. **CRITICAL:** Both `get_latest_frame()` and `get_latest_chunk()` MUST use `nb::gil_scoped_release` so the sleep does not block the Python event loop.

**`CMakeLists.txt`:** Standard nanobind build. Output a Python module named `cpp_hal`."

### Prompt 4: Sensor Worker (Multi-Sensor, Registry Pattern)

"Create `services/sensor_worker.py` inheriting from `BaseService`. It requires the `EventBus` and `RobotConfig` on initialization.

In `start()`, read `RobotConfig.sensors`. For each sensor config entry:
1. Determine if it is a frame sensor or video sensor based on `type` field:
   - `'rgb_photo'`, `'thermal'`, `'multispectral'`, `'lidar'` → frame sensor. Call `cpp_hal.create_frame_sensor(...)`.
   - `'rgb_video'` → video sensor. Call `cpp_hal.create_video_sensor(...)`.
2. Launch a **separate async task** for each sensor. This means a robot with 4 sensors has 4 concurrent producer tasks, all feeding the same `EventBus.storage_queue`.

Each **frame sensor task** loops:
1. `frame = await asyncio.to_thread(sensor.get_latest_frame)`
2. Construct a `SensorSample` dataclass from the C++ `FrameData` fields and the `sensor_config.reductstore_bucket`. Set `time_end=None`.
3. `await bus.storage_queue.put(sample)`.

Each **video sensor task** loops:
1. `chunk = await asyncio.to_thread(sensor.get_latest_chunk)`
2. Construct a `SensorSample` dataclass from the C++ `VideoChunkData` fields and the `sensor_config.reductstore_bucket`. Set `timestamp=chunk.time_start` and `time_end=chunk.time_end`.
3. `await bus.storage_queue.put(sample)`.

In `stop()`, cancel all sensor tasks and await their completion."

### Prompt 5: Zenoh Network Worker (Broadcast Queryable)

"Create `services/network_worker.py` inheriting from `BaseService`. It requires `db_client`, `RobotConfig`, and a ReductStore client instance on initialization.

In `start()`:
1. Open a Zenoh session in **peer mode** (no broker). Use default scouting (UDP multicast on 224.0.0.224:7446).
2. Declare a queryable on key expression `robot/*/query/spatial`. This is the **broadcast scatter-gather** pattern — every robot registers on this same pattern, every query reaches every robot.

When a query arrives:
1. Parse JSON payload into a `SpatialQuery` dataclass. Expected fields: `bbox` (array of 4 floats: [lon_min, lat_min, lon_max, lat_max]), `time_start` (ISO string), `time_end` (ISO string), optional `sensor_type` (string).
2. Call `await db_client.query_spatial(query)` which returns `list[StoredRecord]`.
3. If no results, reply with `{'robot_id': config.robot_id, 'results': []}`.
4. If results exist, for each `StoredRecord` fetch the binary blob from ReductStore using `record.bucket` and `record.file_id`. Build a `QueryResult` dataclass with the record and base64-encoded binary.
5. Serialize the list of `QueryResult` to JSON and reply via Zenoh.

**Important:** Query handling must not interfere with the storage pipeline. The queryable callback runs on the async event loop independently of the storage_queue consumer. The SpatiaLite WAL mode allows concurrent reads (from queries) and writes (from storage worker)."

### Prompt 6: Main Entrypoint (Wiring)

"Create `main.py`.

1. Load `RobotConfig` from `robot_config.yaml` using `core/config.py`.
2. Initialize `EventBus`.
3. Initialize `db_client` (connects to SpatiaLite, creates tables).
4. Initialize ReductStore client. For each bucket in the config, ensure it exists and set the FIFO quota.
5. Instantiate services:
   - `SensorWorker(bus, config)` — reads config, creates C++ sensors from registry.
   - `StorageWorker(bus, db_client, config, reductstore_client)` — consumes queue, writes blobs + metadata.
   - `NetworkWorker(db_client, config, reductstore_client)` — handles spatial queries.
6. Collect all services into a list.
7. In `async main()`: call `start()` on each service.
8. Handle `KeyboardInterrupt` / `SIGINT`: call `stop()` on each service in reverse order for graceful shutdown.
9. Log startup banner with robot_id, robot_type, number of sensors, and configured buckets."

### Prompt 7: Integration Smoke Test

"Create a `tests/test_integration.py` that:
1. Loads a test config with 2 sensors (one frame, one video).
2. Starts all services.
3. Waits 5 seconds for data to accumulate.
4. Sends a Zenoh spatial query covering the mock GPS area and verifies that results come back with both metadata and binary data.
5. Verifies SpatiaLite has records with valid SRID 4326 geometries.
6. Verifies ReductStore buckets contain blobs.
7. Shuts down cleanly.
8. Print summary: number of records ingested, query latency, memory usage (via `resource` module)."
