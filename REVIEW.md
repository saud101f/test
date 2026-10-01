# Sheep monitoring — architecture & reliability review

Review date 2026-10-01 · input: `Sheep_Claude_Review_2026-10-01` (MANIFEST.json: all 42 listed files re-hashed, 0 mismatches) · scope: ESP32 gateway v1.3, Python file server, MG24 collar snapshots, evidence logs 01–12.
Companion documents: [`ENHANCEMENT_PLAN.md`](ENHANCEMENT_PLAN.md) (ordered changes + rollback) and [`HARDWARE_TEST_PLAN.md`](HARDWARE_TEST_PLAN.md) (measurable tests). Reproductions and tools: [`review_tests/`](review_tests/README.md).

Nothing supplied was modified, flashed, deployed or contacted. Operational identifiers (IP, MACs, project, user, token) are deliberately not repeated here.

**How to read confidence labels.** *Proven* = follows from source code/primary documentation plus the logs, or reproduced on the host. *Likely* = best fit to evidence, not excluded alternatives. *Hypothesis* = plausible, needs a named measurement. *Unknown* = evidence absent. Host tests/compilation are **not** hardware validation; no hardware test was run for this review.

---

## 1. Executive summary

**Verdict.** The storage/ACK design is sound and survived adversarial testing; the system is **not yet a working end-to-end archive** because (a) upload throughput is below the data rate even in the best observed case, so the backlog can never drain, (b) the BLE failure and the TLS stall are still unexplained, and (c) several silent time-bombs (SD full ≈ 42 days, VM disk ≈ 32 days, server certificate ≈ Sep 2027) have no alarm.

| | |
|---|---|
| **Works** (evidence) | Durable commit→checkpoint→ACK ordering: 798 injected crash runs (399 syscall points × clean/torn; real `CsvStore.h` compiled on host) → 0 lost, 0 duplicated acknowledged batches; fragment assembler = reference model on 200 000 fuzzed streams; server: TLS verification on, token compare constant-time, temp-file + exclusive-link publish, SHA-256 + CSV validation, 10/10 host tests pass; v1.3 three-collar steady state for the ~4 min logged (0 disconnects/overflow); MG24 01/02/03 sketches are functionally identical. |
| **Fails / proven defects** | Upload < capture rate (measured 5.5 kB/s mean vs 8.89 kB/s generated); same oldest file retried forever (121 attempts, others starve); pre-auth single-slot DoS on the server (reproduced); first-boot torn checkpoint halts collection permanently (reproduced, fix verified on host); SD fault = permanent halt with no self-recovery or remote signal; diagnostic `code=48` is a socket fd, not an error. |
| **Unknown** | Why Sheep-03 loses links (supervision timeout 0x208) only when ≥2 links are up; why TLS writes stall after 2.5–3.5 kB; actual RTT, packet loss, average SD commit time, heap behaviour beyond minutes, flashed MG24 revisions, real-data compressibility. |
| **Single best next test** | **HT-01** — upload the stuck 9.85 MB file with the collars *physically off* (no BLE load), `ss -ti` sampling on the VM. It splits "network/TLS/heap" from "BLE coexistence", yields the true TLS+Wi-Fi throughput ceiling that every enhancement decision depends on, needs no code change (v1.3 already has `collars none` / `uploads on`) and puts no data at risk (SD files are copied first). |

**Top findings** (details §4):

| ID | P | Finding | Status |
|---|---|---|---|
| F1 | P1 | Upload throughput cannot keep up with capture; every retry also re-hashes the whole file and restarts from byte 0 | Proven (arithmetic + logs) |
| F2 | P1 | `chooseFile()` always returns the oldest unsent file → one failing file starves the queue | Proven (code + log) |
| F3 | P1 | No retention/alarm: SD and VM fill in ~6 weeks; SD-full stops ACKs → collar data loss within 55 s | Proven (arithmetic) / Likely (exact dates) |
| F4 | P1 | TLS stall is the 15 s *no-progress* write timeout inside the core, not a TLS/alloc error; cause of the stall is open | Proven (mechanism) / Hypothesis (cause) |
| F5 | P1 | Sheep-03 supervision timeouts: pairwise (needs another link), same collar firmware, harmonic intervals 80/120/160 ms, Wi-Fi + BLE + TLS all pinned to core 0 | Hypothesis (ranked, §5.2) |
| F6 | P2 | Heap during TLS is 6–17 kB with largest free block 0.6–7 kB; idle largest block shrinks 55→35 kB over hours | Proven (log) / Hypothesis (causal link) |
| F7 | P2 | `busy` flag taken before authentication → unauthenticated TLS client blocks all uploads | Proven (host repro) |
| F8 | P2 | SD fault is terminal until manual reboot; only visible on serial | Proven (code) |
| F9 | P2 | Certificate/token lifecycle: server cert ≈ 1 year, token in plain flash, one shared token, no expiry monitoring | Proven (design) |
| F10 | P3 | First-boot crash window leaves an empty checkpoint file → "no valid checkpoint" halt | Proven (crash matrix) |
| F11 | P3 | Misc: dir scan under `sdMutex` grows with file count; SD task stack margin 1.1 kB; ring size is duplicated in two codebases (`bufferCount <= 1365`); timestamps are receipt-time | Proven / optional |

---

## 2. Method and what was (not) executed

| Activity | Done here? | Result |
|---|---|---|
| Re-hash MANIFEST (42 files) | yes | 0 mismatches |
| `python work/test_hourly_server.py` (Python 3.11, cryptography installed) | yes | 10/10 OK |
| Primary-source checks: arduino-esp32 **3.3.11** `NetworkClient*.cpp`, `ssl_client.cpp`, `SD.cpp`; NimBLE-Arduino **2.5.1** `NimBLEClient.*`, `nimconfig.h`; ESP-IDF 5.5 coexistence guide, lwIP Kconfig, `vfs_fat.c` | yes (fetched from the GitHub tags; IDF 5.5 is *assumed* to be what core 3.3.11 bundles — not verified) | §5.3 |
| Crash-injection of the real `CsvStore.h` on the host | yes | `review_tests/csvstore_crash` |
| Fuzz of the real `Collar::handleNotify()` | yes (+ mutation self-test) | `review_tests/ble_assembly` |
| Server pre-auth slot repro, capacity model, binary→CSV byte-exactness, evidence re-derivation | yes | `review_tests/server`, `capacity`, `evidence` |
| Compile ESP32/MG24 sketches | **no** (no toolchain; handoff states a v1.3 compile passed) | — |
| Any hardware, BLE/Wi-Fi coexistence, SD power-loss, long-run throughput | **no** | HARDWARE_TEST_PLAN |

Host-model limits: the crash harness assumes POSIX semantics, "files revert to last-fsynced content", and that rename/create/truncate are immediately durable (FATFS writes directory entries through, but this is *not proven for real cards*); card-internal write caches, torn FAT updates and wear are out of scope.

---

## 3. Architecture map

### 3.1 Components, tasks, locks (line numbers: `ino` = `ESP32_Hourly_SD_Cloud.ino`, `HR` = `HourlyRuntime.h`, `CS` = `CsvStore.h`)

```
MG24 x3 ──BLE notify (3 fragments/batch)──► NimBLE host task ─handleNotify()─► per-collar mailbox
   ▲                                                                              │ (portMUX)
   └──────── ACK (write-no-response, 4 B) ◄── collar worker task ◄───────────────┘
                                                   │ deliveryConfirmed()
                                                   ▼
                                       deliveries[3] (deliveryMutex) ──► sd_writer task
                                                                         │ sdMutex
                                      CSV append+fsync → checkpoint commit (fsync+readback) → confirmed=true
                                                                         ▼
                      /sd/hourly/*.open → *.csv (sealed) ──► hourly_upload task ──TLS SFU2──► VM sheep-files.service
```

| Task | Created | Core | Prio | Stack (min free seen) | Role |
|---|---|---|---|---|---|
| `sd_writer` | HR:233 `xTaskCreate` | unpinned | 2 | 5120 (1148–1180 B) | commit batches, close file at hour edge (HR:33-63) |
| `hourly_upload` (`wifiTask`) | ino:511 | **0** | 1 | 6144 (2004–2172 B during TLS, 3340 idle) | Wi-Fi, SNTP, hash, TLS upload (HR:188-206) |
| `sheep01/02/03` | ino:539 | unpinned | 1 | 4096 (1516–1624 B) | connect/discover/subscribe, ACK write, 10 s no-data watchdog (ino:392-464) |
| `loopTask` | Arduino | 1 | 1 | default | serial diagnostics (ino:546) |
| NimBLE host | library | **0** (`nimconfig.h` default `CONFIG_BT_NIMBLE_PINNED_TO_CORE 0`, line 212) | lib | 4096 | runs `handleNotify` |
| Wi-Fi/lwIP, BT controller | IDF | **0** by default (`ESP_WIFI_TASK_PINNED_TO_CORE_0` default) — *prebuilt Arduino config not verified* | high | — | radios |

| Lock | Protects | Notes |
|---|---|---|
| `sdMutex` (HR:224 create in `beginStorage`) | every FATFS operation incl. `csvStore`, directory scan, upload reads, `.ok` marker | FreeRTOS mutex (priority inheritance). Never held across network calls (HR:96-99, 178-186). |
| `deliveryMutex` (ino:503) | `deliveries[3]` | only short copies |
| `connectMutex` (ino:505) | serialises BLE connection establishment (try-lock, ino:432) | one absent/flapping collar delays others' reconnects by up to 15 s + discovery |
| `mailboxMux` (per collar) | `pendingPacket`, `ackPending`, `packetToken`, epoch | spinlock critical sections copy 617 B (≈µs) |
| atomics | `uploadsEnabled`, `collarMask`, `connectedMask`, counters | |

No nested lock acquisition exists (storage: `deliveryMutex` → release → `sdMutex`), so deadlock is excluded by inspection; priority inversion on `sdMutex` is covered by mutex inheritance.

### 3.2 State machines

**Collar worker** (`Collar::service`, ino:392): `disabled` → (mask) disconnect/no retries · `disconnected` → backoff 2 s (ino:33,426) → try-lock `connectMutex` → `connect(addr, deleteAttributes=true, async=false, exchangeMTU=true)` (ino:265; NimBLE timeout 15 000 ms, retries 0) → `getService`/`getCharacteristic`×2/MTU≥247/CCCD present/`subscribe(response=true)` (ino:273-308) → `ready`. In `ready`: no valid batch for 10 s ⇒ forced disconnect (ino:457); else `processPendingPacket()` every 5 ms (ino:331).

**Batch assembly** (`handleNotify`, ino:178-255): fragment *i* of 3, 8-byte header `magic 0x3353, batch u32, index u8, count u8` + ≤236 B; accept index 0 only with exact length; index *k* only if `nextFragment==k` and same `batchNumber`; on the 3rd fragment validate version=3, `deviceID==DEVICE_ID`, `sampleCount==25`, `bufferCount<=1365` (ino:229), per-sample `sequence`/`acquiredUs` strictly increasing (ino:230-235); any failure resets assembly and counts `invalidPackets`. The valid packet overwrites the mailbox (ino:247-252) and sets `ackPending`. *Fuzz result: identical to the reference model on 200 000 streams (drops, duplicates, reorder, truncation, bad count, batch mismatch); the harness detects a deliberately broken mutant (11 945 wrong accepts).*

**ACK path** (`processPendingPacket`, ino:331-390): ACK is written **only if** `deliveryConfirmed(packet)` (ino:356) → `sd_writer` has finished `append()` for this exact packet (HR:20-31, 33-63). Write is `writeValue(4 B, response=false)` (ino:374): success means *queued*, not delivered. Collar resends after 2 s (MG24 `ACK_TIMEOUT_MS`, MG24_Sheep03.ino:29,296) and the gateway re-ACKs idempotently (slot still `confirmed`, or `CsvStore` key match).

**Storage commit** (`CsvStore::append`, CS:120-143): (1) duplicate check against persisted `keys[dev]` (boot, batch, CRC32 of samples) → exact repeat returns *true without writing* (CS:122-125); (2) `rotate()` (hour/sync change → `seal()` → new `.open` with header → `commit()`) (CS:103-119); (3) 25 rows `write()`; (4) `fsync(csv)` (CS:140); (5) `last=key; commit()` = write 512-B checkpoint to slot `generation&1`, `fsync`, re-open and compare (CS:49-60); (6) only then `confirmed=true` and ACK.

**Checkpoint/recovery** (CS:76-102): two alternating 512-B slots (`SCP1`, generation, committed offset, active file name, last batch key per collar, CRC32). `begin()` loads both, takes the higher valid generation, truncates the active file to the committed offset, fsyncs, seals it. Both invalid + non-empty directory ⇒ fail-stop ("preserve card").

**Upload** (`uploadFile`, HR:117-187): open (≤128 MiB) → **full-file SHA-256 pass** (HR:124-136) → TLS connect (15 000 ms) → `SFU2`+token → `OKAY` → metadata `!HQ32s` + name → `SEND`/`DONE` → body in 1 KiB reads / 512-B TLS writes, **wait for `MORE` after every 4096 B** (HR:152-170) → final `DONE`+digest → write `.ok` marker (digest) with fsync. `wifiTask` retries every 60 s, 1 s after a success (HR:192-203).

### 3.3 Packet / file formats

| Item | Layout (little-endian) | Source |
|---|---|---|
| Batch (617 B) | `ver u8=3, id u8, boot u64, batch u32, count u8=25, bufferCount u16`, 25×`{ax,ay,az,gx,gy,gz int16; acquired_us u64; sequence u32}` = 24 B | ino:51-74, MG24:32-55 |
| Fragment | 8-B header + ≤236 B (3 notifications, MTU 247) | ino:76-96 |
| ACK | 4-B batch number, GATT write-no-response | ino:366-374 |
| CSV (18 columns, 118.5 B/row observed) | `gateway_received_gmt3, gateway_uptime_ms, device_id, boot_id, batch_number, buffer_count, sample_index, sample_sequence, acquired_us, x/y/z_mg, gyro_*_raw, g*_dps(=raw×0.07)` | CS:8, 128-139 |
| Checkpoint (512 B) | `SCP1, gen u64, offset u64, name[128], 3×{boot u64,batch u32,crc u32}, pad 312, crc32` | CS:18-23 |
| SFU2 | `SFU2`+64-char token → `OKAY`; `!HQ32s`+name → `SEND`/`DONE+digest`; body with `MORE` per 4096 B; `DONE+digest` | HR:144-175; server:94-143 |

*Derivable semantics worth documenting.* Row wall-clock is **receipt** time (to 1 s) with `gateway_uptime_ms` (1 ms) — not acquisition time. Because `bufferCount` is the collar queue depth at transmit (MG24:315) and the batch is the oldest 25 samples, sample *i* was acquired ≈ `rx_time − (bufferCount − 1 − i)·40 ms − BLE/ACK latency` (valid if no overflow in between). Unsynced rows have blank wall-clock; files named `unsynced_<hour>` share the boot nonce with later synced files of the same boot, so `uptime` pairs can re-anchor them.

### 3.4 MG24 snapshots vs reference

| File | SHA-256 (prefix) | Difference |
|---|---|---|
| `MG24/MG24.ino` (ID 1), `MG24-2/MG24-2.ino` (ID 2) | `ebaebdb4…`, `3f330071…` | identical to each other except `DEVICE_ID`, comment and name strings (diff proven) |
| `MG24-3/MG24-3.ino` = `mg24_reference/MG24_Sheep03.ino` | both `577bdfcf…` | byte-identical to each other; differs from 01/02 **only** by BLE identity-address logging (+3 globals, 17 lines in `sl_bt_evt_system_boot_id`, 12 lines in `loop`) |

So the protocol, ring (1365 samples = 54.6 s), ACK logic and advertising (100 ms) are the same on all three, and the only code-level asymmetry between collars is on the **gateway** (`interval` 80/120/160 ms, ino:118-119). *Uncertainty:* these are workspace snapshots, not flashed images; the installed MG24 core/Silabs stack/LSM6DS3 library versions and the actual flashed revision of each board are **unverified** — the 01/02 snapshot also *cannot* print its address, which is why the unlabeled log 12 cannot be assigned to a collar. Remedy: print `__DATE__ __TIME__` + source hash + BLE address at boot on every collar (ENHANCEMENT_PLAN E0).

---

## 4. Prioritised findings

Format: **Trigger · Evidence · Consequence · Confidence · Minimal remedy · Verification**.

### F1 (P1) Upload cannot keep up with capture — Proven
- **Trigger**: any hour with 3 collars running (75 rows/s).
- **Evidence**: 8.89 kB/s generated (`capacity_model.py`; 118.5 B/row from 968 578 B / 8 175 rows); v1.3 upload 81 920→913 408 B in 151 s = **5.5 kB/s mean, 8.6 → 4.1 kB/s decaying** (log 11); first success 968 578 B in ~125 s ≈ 7.4 kB/s (log 01); pre-hash 9.85 MB in 35 s = 280 kB/s (logs 10/11) ⇒ 114 s *per attempt* for a full 32 MB hour file, repeated at every retry (HR:124-136). Window design HR:152-170 / server:129-136: 4096-B stop-and-wait; ideal ceiling `4096/(RTT+delayed-ACK)` = 15.8 kB/s at 0.22 s RTT (RTT **not measured**), and the first 10 s window already runs at 8.6 kB/s ≈ 0.48 s per window.
- **Consequence**: net backlog growth 3.4 kB/s at the mean rate (12 MB/h); even at the best 8.6 kB/s the backlog never shrinks (−0.3 kB/s). 15 queued files cannot drain. At 12 / 20 / 40 kB/s the 15-file backlog clears in 43 h / 12 h / 4.3 h.
- **Remedy** (ENHANCEMENT_PLAN E5–E8): cache the digest (sidecar) so retries do not re-hash; larger/continuous window + resume offsets; send a **CSV-preserving compact encoding** (≈4.7× fewer bytes, server regenerates byte-identical CSV, verified for the real `CsvStore` output and all 65 536 gyro values — `binary_to_csv_roundtrip.py`).
- **Verification**: HT-01 (ceiling), HT-10 (≥ 20 kB/s mean, ≥ 2.2× capture, full-hour file completes, 15-file backlog drains while collecting).

### F2 (P1) Head-of-line blocking in the retry scheduler — Proven
- **Trigger**: any file whose upload keeps failing.
- **Evidence**: `chooseFile()` picks `strcmp(name) < 0` (HR:100-115) every 60 s with no per-file failure memory (HR:197-202); log 08: 121 consecutive attempts on the same file while `queued_files` grew 6→10 and the small, valid files behind it never tried.
- **Consequence**: one bad file halts the whole upload pipeline; backlog and exposure grow silently.
- **Confidence**: high. **Remedy**: RAM-only failure counter + exponential backoff per file, rotate to the next eligible file (E3). **Verification**: unit-level host test of the scheduler logic + HT-10 with a deliberately blocked file.

### F3 (P1) No retention policy, no capacity alarm — Proven arithmetic, Likely dates
- **Evidence**: 768 MB/day of CSV; SD 30 424 MiB full in **41.6 days**; VM ≈ 32 days (30 GB disk, 1 GiB server reserve server:62,120, ~4 GB OS *assumed*). SD-full → `open(O_EXCL)` or `write` fails (CS:114-115, 137) → `sdHealthy=false` → ACKs stop → every collar overflows in 54.6 s (ring) — data loss, silently (message only on serial, HR:45).
- **Remedy**: opt-in retention that deletes only files with a verified `.ok` marker *and* a cloud-confirmed copy older than N days (never automatic by default), a capacity alarm well before full, and the compact encoding (SD fills in ~194 days). Owner decision required for any deletion (E4).
- **Verification**: HT-06 (fill behaviour on a scratch card), alarm test.

### F4 (P1) The observed TLS "write failed" is the core's 15 s *no-progress* timeout — Proven mechanism; cause Hypothesis
- **Evidence (source, core 3.3.11)**: `NetworkClientSecure::write` logs *"Closing connection on failed write"* (`NetworkClientSecure.cpp:248`) iff `send_ssl_data()` returns <0. That happens (a) when no `mbedtls_ssl_write` progress for `socket_timeout` ms (`ssl_client.cpp:453-455`, logged only at *verbose*), or (b) via `handle_error()` which **always logs at error level** (`ssl_client.cpp:41`) — except for peer close-notify. Log 08 has 111 "Closing connection…" lines and **no** `_handle_error` line besides one handshake-EOF ⇒ the stalls are overwhelmingly (a). `socket_timeout` = the `connect()` timeout argument = **15 000 ms** (`NetworkClientSecure.cpp:129-130` → `ssl_client.cpp:116`). Server side (log 09, 40 attempts): 35 `IncompleteReadError` (client closed) after **18–29 s (median 22 s)** from "Receiving", bytes received 2560/3072/3584 (5–7 TLS records), twice 7168/7680 (one full 4096 window + 3–3.5 kB), five 30 s timeouts with 0 bytes — i.e. ≈ 15 s stall + a few seconds of trickle at ≈ 0.3–0.9 kB/s. *All received sizes are multiples of 512.*
- **Not the cause**: authentication/protocol (metadata accepted), server timeouts, SFU1/SFU2 mismatch (fixed earlier), per-record size.
- **Consequence**: mbedTLS reported no error; TCP made no forward progress for 15 s on a connection that had just delivered 2.5–3.5 kB.
- **Candidate causes** (ranked; discriminators in §5.1): H-T1 Wi-Fi/BLE airtime and retry loss under coexistence; H-T2 small-flow TCP loss recovery (lwIP RTO backoff, ≤ 4 segments in flight, no fast retransmit) on a lossy path; H-T3 heap exhaustion in lwIP/Wi-Fi buffers (6–17 kB free, largest block 0.6–7 kB during TLS); H-T4 path-MTU/MSS black hole (low); H-T5 server stall (excluded: server logs show prompt reads, tiny work per chunk).
- **Confidence**: mechanism high; cause open. **Remedy**: first measure (E0: per-window timing, `errno`/`SO_ERROR` *before* the core closes the socket, heap watermark; VM `ss -ti`/pcap), then fix whichever hypothesis wins. **Verification**: HT-01, HT-03.

### F5 (P1) Sheep-03 connection-supervision timeouts — Hypotheses, no certainty
- **What is established** (all from logs 02–08, 10/11 and source): disconnect `520 = 0x208` = host base `0x200` + HCI "connection timeout" (NimBLE returns it as local supervision timeout; the 4 s value is requested at ino:119 and confirmed by `BLE PARAM timeout_ms=4000`). In the 3 h 50 min failure run Sheep-03 *connected successfully* 1040 times (discovery, CCCD write, MTU 247 all succeeded) but the link lived only **2–8 s (median 4 s)**, connect took 4–11 s (median 7 s): a ~13 s cycle for hours, **zero** valid Sheep-03 batches, while Sheep-01/02 ran at buffer=25. Collar-side (log 02): 38 reconnect-resend cycles in 522 s, 0 ACKs, ring full. Isolation (logs 03–07): Sheep-03 *alone* drains its ring (74 ACKs); adding 01 (even alone, even with uploads idle and 60 kB heap) breaks it again; a powered-off 02 still generated connection attempts. Sketches on all collars are functionally identical (§3.4). v1.3 (same intervals 80/120/160 ms) ran ≥4 min with all three healthy (log 10/11). During the *one successful* v1.2 upload Sheep-03 flapped 9 times (log 01): flapping alone did not block a 125 s TLS transfer.
- **Ranked hypotheses**:
  1. **Multi-link scheduling collision (central side).** Only pairs of links fail; intervals are 80/120/160 ms — 160 is an exact multiple of 80, so connection events of links 1 and 3 collide at the same instants for as long as their phase offset overlaps (all anchors come from the one ESP32 clock, so relative drift is zero and a bad phase persists until reconnection, which re-rolls it). Catch-up bursts (collar sends batches back-to-back after each ACK, MG24:282-283) enlarge event length. Predicts: pair-specific, bimodal behaviour, fixed by equal/non-harmonic intervals. *Test: HT-04.*
  2. **Coexistence + initiator pressure.** Wi-Fi *connected* + BLE *connected* ⇒ time-sliced RF, "each 50 % of a ≥100 ms period" (ESP-IDF coexistence guide); Espressif recommends Wi-Fi and the BT controller/host on **different CPUs**, but here Wi-Fi, controller, NimBLE host *and* the TLS uploader are all on core 0. An absent/flapping collar keeps the initiator scanning with a 20 ms window every 100 ms (ino:119: 160/32 units of 0.625 ms) for 15 s then 2 s backoff ≈ 88 % of the time, serialised by `connectMutex`. *Test: HT-05 (absent collar, `collars` mask), HT-04 (core placement).*
  3. **Host buffer exhaustion** (NimBLE msys pool 12 × 256 B, `nimconfig.h:254-260`; 3 × 244-B notifications per batch per link) when the host task is delayed. *Unverified; needs buffer statistics.*
  4. **Collar RF/placement or flashed-revision difference** (onboard antenna, RF switch MG24:517-521). Weakened by "03 alone works", not excluded. *Test: swap boards/IDs.*
- **Not established**: DLE/PHY actually negotiated (never logged; `setDataLen` is never called), RSSI per link, collar-side disconnect reason, when the 80/120/160 "timing experiment" (ino:116) was introduced relative to the first 0x208.
- **Remedy**: instrument (E0), then A/B interval/core/initiator experiments (E1/E9). **Verification**: HT-02/04/05 acceptance: 0 × 0x208 and 0 overflow in 4 h; reconnect p95 < 15 s.

### F6 (P2) Heap/stack margins — Proven values, causal link Hypothesis
- Idle (fresh v1.3 boot) 59.7 kB free / **55.3 kB** largest; after hours of v1.2 flapping/uploads 59.7 / **34.8 kB**; **during TLS 6.1–17 kB free, largest 0.6–7.4 kB** (log 11: min 6 248/596). TLS therefore costs ≈ 50 kB of a 60 kB budget. Stacks: `sd_writer` 1.1 kB free of 5 kB (77 % used), `sheep0x` ≈1.5 kB free of 4 kB. `deleteAttributes=true` on every reconnect (ino:265) re-allocates GATT objects (heap churn at 13 s cycles). Allocation failures inside TLS would have logged via `handle_error` (none seen), so the heap link to F4 is **not** demonstrated.
- **Remedy**: PSRAM module or leaner TLS (persistent session/keep-alive is *not* leaner), keep GATT cache on reconnect, log `heap_caps_get_minimum_free_size`, guard writes with a "largest block ≥ 4 kB" check (also a test of H-T3) — E0/E2.
- **Verification**: HT-03/09: min free ≥ 12 kB and largest ≥ 4 kB during upload; stack min-free ≥ 1 kB all tasks.

### F7 (P2) Server `busy` slot is taken before authentication — Proven (reproduced)
- `self.busy = True` at server:89, authentication read at 94-96 with a 30 s timeout. `review_tests/server/repro_busy_slot_dos.py`: a client that completes only the TLS handshake blocks the genuine gateway for as long as it stays connected (legit auth denied while attacker connected; works again after it leaves). The firewall policy (country-level per the handoff) reduces but does not remove exposure; reconnecting costs the attacker nothing.
- **Remedy**: set `busy` only after a valid token, cut the pre-auth read to ≈5 s, optionally require a client certificate (mTLS) so unauthenticated peers die in the handshake (E10). **Verification**: rerun the repro (must show legit success), 10 existing tests, 1 000 junk connections/min.

### F8 (P2) SD fault is terminal and silent — Proven
- Any append/seal failure → `sdHealthy=false`, `break` (HR:45-46, 57); nothing re-initialises it; ACKs stop; collars overflow after 54.6 s; only a serial line reports it. **Remedy**: after a fault, log + short back-off + `ESP.restart()` (recovery is proven crash-safe below), count restarts, expose state (status LED/NVS counter/cloud heartbeat) — E4. **Verification**: HT-07.

### F9 (P2) Certificate/token lifecycle — Proven design facts
- Embedded CA (RSA-2048, SHA-256, CA:TRUE pathlen 0, **valid 2026-09-26 → 2036-09-23**, verified with `openssl`). Server certificate: *one-year, issued for the IP* (handoff) ⇒ expires ≈ Sep 2027 (exact date not in the bundle). Because the CA is pinned, renewing only the server certificate needs **no firmware change** — but nothing monitors expiry, and a failed handshake looks like any upload failure. Clock dependence: uploads wait for SNTP (`clock_ready`, HR:198) — fail-closed, correct. Token: 64 hex chars, static, compiled into flash (readable with physical access; no per-device identity, no rotation path). TLS verification is on (`setCACert`, no `setInsecure` anywhere).
- **Remedy**: expiry check in `check_cloud.py` + calendar/alert 60 days before; rotation runbook (new server cert signed by same CA, `systemctl restart`); per-gateway token support server-side; optional mTLS (E10). Owner approval required before touching the VM.

### F10 (P3) First-boot crash window — Proven (reproduced), fix verified on host copy
- `begin()` on a blank card creates `checkpoint1.bin` then fsyncs (CS:86 → 49-60). If power fails between the create and the fsync, the next boot finds a non-empty directory with no valid checkpoint and halts (CS:81-85). Crash matrix: 4 of 798 injected points. Fix: ignore `checkpoint*.bin` when deciding "card has data" (1 line; host copy verified: 798/798 pass).

### F11 (P3 / optional)
- `chooseFile()` scans the directory and `stat()`s every file under `sdMutex` each minute (HR:100-115): cost grows with file count (≈ 24/day) and blocks commits; not measured. Keep an in-RAM queue and scan in slices.
- Ring size is hard-coded twice (`RING_CAPACITY` MG24:26; `bufferCount <= 1365` ino:229). Enlarging the collar ring (MG24 has RAM headroom — *unverified*) without the gateway change would make every batch "invalid".
- `check_cloud.py` is fine; `validate_csv` is **not** a risk: a 270 000-row hour validates in 1.5 s on the review host (ESP32 waits 30 s for `DONE`).
- systemd unit (service:14-21) is already sandboxed; `RestrictAddressFamilies`, `ProtectKernel*`, `SystemCallFilter` are cheap extra hardening.
- MG24 03 prints a hard-coded "(ID=3)" (cosmetic).
- Row timestamps are receipt time (see §3.3).

---

## 5. Deep dives

### 5.1 Why TLS writes stall: evidence table

| Observation | Source | Consistent with |
|---|---|---|
| Fail after 5–7 × 512 B within a window (35/40; 33 in the first window, 2 in the second), never <2560 except 0 B (5/40) | log 09 | stall *inside* the first (or second) window, not at connect/auth |
| 18–29 s between "Receiving" and failure | log 09 | 15 s no-progress timeout + short trickle |
| 5/40 server 30 s timeouts with 0 B | log 09 | the `SEND` reply or first window never flowed either direction |
| Core logs "Closing connection on failed write", no `_handle_error` | log 08 vs `ssl_client.cpp:41,453` | timeout path (a), not an mbedTLS error |
| 968 kB file succeeded in 125 s while Sheep-03 flapped | log 01 | BLE flapping is not sufficient to stall TCP |
| 120 failed attempts, all with Sheep-03 flapping, 0 successes | log 08 | flapping raises the stall hazard, or something else changed between runs (uptime, heap fragmentation 55→35 kB) |
| v1.3 with all collars stable ran 150 s at 8.6→4.1 kB/s without a stall | log 11 | hazard lower without BLE churn; rate decays as heap shrinks |
| `code=48 detail=UNKNOWN ERROR CODE (0030)` | log 08 | **not an error**: `sslclient->last_error` is only ever assigned the return of `start_ssl_client()` (`NetworkClientSecure.cpp:156,197`), which on success is the lwIP **socket fd** (`ssl_client.cpp:322`); `mbedtls_strerror(48)` prints "unknown". |

Discriminating measurements, cheapest first: (1) `ss -ti` on the VM once per second during an attempt → server-side `rtt`, `retrans`, `mss`, `bytes_received`; (2) short `tcpdump -s 96` of one failing attempt (retransmit pattern, ICMP, window); (3) HT-01 (collars off) vs HT-03 (collars on); (4) timing around each `c.write` and a "largest free block" guard (E0/E2). Interpretation table is in HARDWARE_TEST_PLAN §HT-01.

### 5.2 Sheep-03: see F5

### 5.3 Timeout units and inheritance (verified against core 3.3.11 / NimBLE 2.5.1)

| Setting (location) | Unit actually used | What it controls | Effect on the observed failure |
|---|---|---|---|
| `client.setTimeout(30000)` (HR:138; v1.2 had `30`) | `Stream::setTimeout(unsigned long ms)` (`cores/esp32/Stream.h:67`) — separate member `Stream::_timeout` | only `readBytes/readString/parse` (`NetworkClient.cpp:515`), which the gateway never calls | **none**: v1.2 = 30 ms, v1.3 = 30 s, both irrelevant |
| `client.connect(host, port, 15000)` (HR:141) | ms → `NetworkClient::_timeout` (`NetworkClientSecure.cpp:129-130`) → `ssl_client->socket_timeout` (`ssl_client.cpp:116`) | TCP connect `select`, **write no-progress limit (`:453`)**, `SO_SNDTIMEO/SO_RCVTIMEO` (`:238-266`, ineffective on the O_NONBLOCK socket, `ssl_client.cpp:91`) | **this is the 15 s that expires** |
| `setHandshakeTimeout(20)` (HR:138) | ×1000 inside (`NetworkClientSecure.cpp:450-452`) = 20 s | TLS handshake | not involved |
| `cloudRead` loop (HR:72-81) | 30 000 ms of *no bytes* | waits for `OKAY/SEND/MORE/DONE` | would fire after 30 s, longer than the 15 s write timeout |
| `setConnectTimeout(15000)` (ino:115) | ms (NimBLE 2.x, `NimBLEClient.cpp:69,584`; passed to `ble_gap_connect`) | BLE initiation duration | up to 15 s of scanning per attempt |
| `setConnectionParams(64/96/128, same, 0, 400, 160, 32)` (ino:119) | 1.25 ms / 1.25 ms / latency / **10 ms** / 0.625 ms / 0.625 ms (`NimBLEClient.cpp:495-500`) | 80/120/160 ms, 4 s supervision, 100 ms/20 ms scan | matches the logged `BLE PARAM` |
| Server `readexactly` 30 s, drain 30 s, handshake 10 s, `wait_closed` 3 s (server:70-74,159,178) | s | per-read limits | the five 0-byte "TimeoutError" lines |

### 5.4 Wi-Fi/BLE/SD/TLS coexistence on this ESP32

BLE+SD+Wi-Fi idle: sustained (hours, logs 08 for 01/02; ~4 min for all three in v1.3). TLS added: heap margin ≈ 6–17 kB, all radio stacks on one core, one 2.4 GHz front-end time-sliced 50/50 when both are connected (IDF coexistence guide), SD max commit 349 ms → 1.18–1.5 s growing over hours (log 08), **average commit time is not logged** (needed: three collars need ≥ 3 commits/s, i.e. < 330 ms each including worst-case stalls). Conclusion: *not proven impossible, not proven safe*; it is the weakest assumption in the design (§HT-03/09). Hardware alternatives are compared in ENHANCEMENT_PLAN §4.

### 5.5 Storage durability audit

*Ordering is correct.* ACK ⇐ `fsync(CSV)` ⇐ rows written ⇐ `commit()` (checkpoint written, `fsync`, re-opened and byte-compared) (CS:140-142, 49-60; HR:33-63; ino:356). The checkpoint alternates between two sectors so the previous valid generation is never overwritten (`generation&1`), recovery truncates the CSV tail to the committed offset, an exact duplicate (boot,batch,CRC) returns success without rewriting, and a *different* payload with the same identity is rejected as a fault (CS:122-125).

*Crash matrix result* (`review_tests/csvstore_crash`): scenario = 12 batches, two collars, three hour files, every mutating syscall (open/write/fsync/rename/ftruncate) × {clean kill, torn half-write}. After each crash a new process recovers, the collar re-sends its last ACKed batch plus everything un-ACKed, the file is sealed, and every batch must appear exactly once with 25 rows. **794/798 pass**; the 4 failures are F10; with the one-line host patch **798/798**. 30 runs leave a header-only unreferenced `.open` file (never contains data rows, harmless, never uploaded).

*Not covered / open*: real FAT behaviour on power loss (directory-entry/FAT updates may reorder; `fsync` → `f_sync`, `vfs_fat.c`; SD cards have private write caches that `fsync` cannot flush), wear (≈3 f_sync + 512 B in-place checkpoint per batch ⇒ ≈ 0.26 M small rewrites/day on the same sectors; use an endurance/industrial card), SD-full and bit-rot handling (fail-stop, correct but terminal — F3/F8), `chooseFile` marker semantics (any 32-byte `.ok` is trusted; the digest inside is not re-checked), unsynced-time files.

### 5.6 TLS/server audit (server file refs)

| Aspect | Finding |
|---|---|
| Transport | TLS ≥1.2 (server:169-171), private CA pinned in the client with `VERIFY_REQUIRED` semantics (`setCACert`); hostname = IP SAN; no insecure path in sources |
| Authentication | static 64-hex token, `hmac.compare_digest` (server:95); sent inside TLS; `SFU1` legacy accepted (no pacing) — consider removing |
| Limits | 128 MiB file, 1 GiB free-space reserve, name regex `sheep_[A-Za-z0-9_-]{1,110}\.csv` (path traversal rejected, tested), one upload at a time |
| Integrity | SHA-256 over the whole body + full CSV validation (header, 18 columns, whole 25-row batches, ranges, gyro conversion) *before* publication (server:139-141) |
| Publication | `mkstemp` in the destination dir → `fsync` file → `os.link` (exclusive, never overwrites) → `fsync` dir → `DONE`; stale `.upload-*` removed at start; retried identical file answers `DONE` after re-fsyncing dir (server:107-119) |
| Gaps | pre-auth `busy` (F7); no total-time cap (30 s per read ⇒ ≥136 B/s is enough to hold the slot); no per-name date plausibility/quota (a token holder can fill the disk down to the 1 GiB reserve); no TCP keepalive; no metrics/alerts; no backups/snapshots of the VM disk (owner decision) |
| Operations | `Restart=on-failure`, `MemoryMax=128M`, `TasksMax=16`, `ProtectSystem=strict` — adequate |

### 5.7 Quantitative summary (`review_tests/capacity/capacity_model.py`)

| Quantity | Value |
|---|---|
| Capture | 75 rows/s × 118.5 B = **8.89 kB/s = 32.0 MB/h = 768 MB/day** (270 000 rows/h) |
| Compact binary (24 B/sample + 16 B/batch) | 633 B/batch → 6.8 MB/h (**4.7×** smaller) |
| Synthetic compressibility (not real data) | zlib-6 3.9–5.1×, xz 5.4–8.2× on the CSV |
| Upload (measured) | 5.5 kB/s mean (8.6→4.1 kB/s) = **0.62× capture** |
| Ideal 4 KiB window at RTT 0.10/0.15/0.22/0.30 s | 29.3 / 21.6 / 15.8 / 12.0 kB/s |
| Pre-hash | 280 kB/s ⇒ 114 s for a 32 MB hour, per attempt |
| SD / VM fill | 41.6 days / ≈32 days (assumptions stated) |
| Collar outage capacity | ring 1365 samples = **54.6 s** per collar (any gateway/SD/BLE outage longer than that loses samples); catch-up alone ≈ 0.32 s per batch (log 03: 25 released per cycle, net −17) ⇒ a full ring drains in ≈ 26 s *if* SD commit keeps up; three collars share one `sd_writer` |
| Server validation | 1.5 s per 270 000 rows (host) — not limiting |

---

## 6. What works / what fails / what remains unknown

**Works (with evidence).** Local durable-then-ACK ordering and recovery (host crash matrix); batch framing/validation; duplicate suppression across boots/retries; server integrity, atomic publication, path safety, size/disk limits (host TLS tests); TLS verification enabled end to end; three collars simultaneously healthy for the ~4 min captured in v1.3; one complete 968 kB verified upload.

**Fails or is deficient.** Throughput vs capture (F1); retry fairness (F2); no retention/alarm (F3); pre-auth slot (F7); terminal SD fault (F8); first-boot window (F10); misleading `code=48` diagnostic.

**Unknown.** BLE root cause (F5) · TLS stall cause (F4) · RTT/loss/average commit time · real compression · long-run heap/fragmentation · flashed MG24 revisions · SD behaviour under true power loss · server certificate end date.

**Evidence gaps — smallest additional measurement** (details in HARDWARE_TEST_PLAN): VM `ss -ti` + short pcap of one failing/passing upload; ESP32 timing/`errno`/heap-min/largest-block per window and BLE RSSI/`getConnInfo`/PHY/DLE per link; collar-side disconnect reason and firmware hash print; histogram (avg/p99/max) of `append()` time; `openssl x509 -enddate` of the server certificate; one real hour CSV (or 1 000 rows) for compression; the date the 80/120/160 ms experiment was introduced.

**Single best next test: HT-01** (§1).
