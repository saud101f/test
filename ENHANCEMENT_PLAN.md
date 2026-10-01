# Enhancement plan — small, ordered, reversible

Companion to [`REVIEW.md`](REVIEW.md) (finding IDs F1–F11) and [`HARDWARE_TEST_PLAN.md`](HARDWARE_TEST_PLAN.md) (test IDs HT-xx). **Nothing here has been applied**; every step needs the owner's go-ahead, and anything touching the VM, firewall, token, certificates or deletion needs explicit approval for that action.

## 0. Ground rules (kept from the brief)

1. A collar ACK is sent **only after** the CSV rows are fsynced **and** the checkpoint is committed. No step below changes that order or makes it depend on the cloud.
2. TLS verification stays on (CA pinned, `VERIFY_REQUIRED`). No `setInsecure`, no plaintext fallback.
3. No automatic deletion of SD or cloud data. Retention (E4e) is opt-in, off by default, and deletes only after a cloud-verified copy.
4. Firmware/CSV/checkpoint compatibility is preserved unless a step says otherwise; every step lists its rollback.
5. Do not pool runs: each hardware run starts from a fresh boot, records bootIDs, and is judged on its own (HT-00).
6. Decide with measurements: steps are grouped into phases with explicit gates; a later phase starts only if its gate is met.

## 1. Order of work (summary)

| # | Step | Phase | Size | Risk | Closes | Gate / verified by |
|---|---|---|---|---|---|---|
| E0 | Instrumentation build (no behaviour change) + VM `ss -ti` sampler | 0 | S | very low | F4 F5 F6 evidence gaps | all HT; blocks nothing |
| E1 | Interval/initiator experiment switches (compile-time profile) | 0 | S | low | F5 | HT-04, HT-05 |
| E2 | Core pinning + "largest block" write guard | 1 | S | low | F4/F5/F6 probes | HT-03, HT-04 |
| E3 | Per-file retry fairness + digest sidecar | 1 | S | low | F1(part) F2 | host test + HT-10 |
| E4 | Operability: first-boot fix, SD-fault restart, status/alarms, opt-in retention | 1 | M | low–med | F3 F8 F10 | HT-06, HT-07 |
| E5 | Window experiment (4→8→16→32 KiB) on SFU2.1 | 2 | S | low | F1 | HT-10 |
| E6 | SFU3: resumable, cumulative-ACK upload | 2 | M | med | F1 | HT-10 |
| E7 | CSV-preserving compact transport (CCT) inside SFU3 | 2 | M | med | F1 F3 | HT-10 + byte-exact test |
| E8 | BLE tuning chosen from HT-04/05 (uniform intervals, GATT cache, absent-collar back-off) | 2 | S–M | med | F5 | HT-02, HT-09 |
| E9 | Server hardening + monitoring (VM, owner-approved) | 2 | M | low–med | F7 F9 | repro + HT-11 |
| E10 | Enlarge collar ring (gateway first, then collars) | 3 | M | med | outage capacity | HT-08 |
| E11 | Hardware decision (PSRAM/S3, LAN-side uploader, Ethernet, SD card class) | gate | — | — | F6 | decision table §4 |

Recommended first sprint: **E0 → HT-01 → E3 → E4(a,b) → E2**. These are the smallest changes with the largest information/benefit ratio and none changes the wire protocol.

---

## 2. Steps in detail

### Phase 0 — measure before changing behaviour

**E0 Instrumentation (firmware `ino/HR/CS` additive; MG24 additive; VM script).**
- ESP32: per-window upload timing (`ms`/4 KiB, min/avg/max per 10 s), time spent inside each `c.write` (a ≈15 000 ms value *proves* the no-progress timeout), `getsockopt(SO_ERROR)` between writes using the public `client.fd()`, `heap_caps_get_minimum_free_size()` + largest block every status line, **SD commit histogram** (count, avg, p50, p99, max of `append()`), per-link RSSI (`client->getRssi()`), `getConnInfo()` interval/latency/timeout, `getPhy()`, link-up duration, disconnect reason string, build ID (`__DATE__ __TIME__`) + source hash.
- Remove the misleading `last_error_hint=48` print (it is the socket fd — REVIEW §5.1); keep `errno` only if captured *before* the core's `stop()`.
- MG24: print build ID/hash + BLE address on every collar (01/02 snapshot cannot today) and the `sl_bt_evt_connection_closed` reason; add `sl_bt_connection_get_rssi` once per second (log only).
- VM (read-only, owner-run): 1 Hz `ss -ti '( sport = :8443 )'` capture (RTT, retransmits, mss, cwnd, bytes_received); optional `tcpdump -s 96 -w` for one attempt.
- **Rollback**: reflash v1.3 / delete script. **Verification**: log volume < 5 % of UART bandwidth; no change in `saved_batches` rate or `max_save_ms` in a 10-minute A/B.

**E1 Interval and initiator switches.** Replace ino:118 with a compile-time profile: `P0` current (80/120/160), `P1` all 80 ms, `P2` all 100 ms (avoids exact harmonics, ≥ 3 × worst event), `P3` P2 + initiator duty reduction (scan window 10 ms/200 ms ⇒ 5 %, absent-collar back-off 2→4→8→16 s cap). Default stays `P0` until HT-04 decides. **Rollback**: constant. **Risk**: reconnect latency vs the 54.6 s collar ring (P3) — bound by HT-05.

### Phase 1 — no protocol change

**E2 Core placement and write guard.** (a) Create `sd_writer`, `hourly_upload` and the three collar tasks pinned to core 1 so core 0 keeps Wi-Fi/BT controller/NimBLE host (Espressif's coexistence guide asks for Wi-Fi and Bluetooth stacks on different CPUs; here everything is on core 0). (b) Optional experiment, library-level: set `CONFIG_BT_NIMBLE_PINNED_TO_CORE 1` in `nimconfig.h` (documented override) — separates host from Wi-Fi; the controller stays on core 0 in prebuilt cores. (c) Before each `c.write`, require `heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL) >= 4096` (wait ≤ 2 s, then abort the attempt with a distinct reason) — this is both a mitigation and a test of hypothesis H-T3. **Rollback**: revert pin arguments/guard. **Verification**: HT-03/HT-04 (stall rate per hour, link drops per hour), stack/heap minima.

**E3 Retry fairness + digest cache.**
- Scheduler (RAM only, `HourlyRuntime.h:100-115,192-203`): keep `{name, failures, next_try_ms}` for the first ~16 candidates; choose the oldest eligible whose `next_try_ms` has passed; after a failure `next_try = now + min(60 s·2^n, 30 min)`; reset on success. Effect: the 968 kB file is no longer blocked behind the 9.85 MB one, and a persistently failing file costs ≤ 1 attempt per 30 min.
- Digest sidecar: after the first full hash write `<name>.sha` (32 bytes, fsync). Retries skip the 35 s/114 s pass (HR:124-136). Older firmware ignores `*.sha` (it only considers names ending `.csv`; `.csv.ok` already coexists the same way).
- **Test (host)**: extract the chooser into a pure function, unit-test starvation/backoff with a simulated failing head file (new `review_tests/` case at implementation time). **Rollback**: delete the sidecars (harmless) / reflash. **Verification**: HT-10 with a deliberately blocked file (queue still drains).

**E4 Operability and safety.**
- (a) **First-boot window (F10)**, proven by `review_tests/csvstore_crash` (4/798 → 0/798): in `CsvStore::begin()` (CS:83) ignore `checkpoint*.bin`:
  ```
  - while((e=readdir(d))) if(strcmp(e->d_name,".") && strcmp(e->d_name,"..")) existing=true;
  + while((e=readdir(d))) if(strcmp(e->d_name,".") && strcmp(e->d_name,"..") && strncmp(e->d_name,"checkpoint",10)) existing=true;
  ```
  Re-run `FIRST_BOOT_FIX=1 python review_tests/csvstore_crash/run_crash_matrix.py <bundle>`.
- (b) **SD fault policy (F8)**: after `sdHealthy=false`, wait 2 s, `ESP.restart()`; persist a restart counter (NVS) and refuse >3 restarts/10 min (then stay halted and signal). Recovery is crash-safe per the matrix, so a restart cannot lose acknowledged data; it *does* cost ≈ boot + BLE reconnect time, which must stay < 54.6 s (HT-07).
- (c) **Visibility**: status LED patterns (SD fault, queue > N, upload failing > X h), `status` command prints queue age/size and last verified upload age; serial status line adds `commit avg/p99/max`.
- (d) **Capacity alarm**: SD free < 20 % or < 10 days at the current rate ⇒ LED + serial; VM-side equivalent in E9.
- (e) **Retention (owner decision, default OFF)**: `retention <days>` runtime command (not persistent across reboot unless explicitly saved) that removes `*.csv` + marker only when `.ok` digest matches the sidecar digest, age > N days, and the cloud has reported the same digest (E6/E7 `DONE`). Never touches `.open`/checkpoints.
- **Rollback**: individually revertible. **Verification**: HT-06 (fill a scratch card, observe alarm, then fault behaviour), HT-07 (power-cut campaign).

### Phase 2 — throughput and data volume (gate: HT-01 + HT-03 results)

**E5 Window experiment (SFU2.1).** Today client window (HR:155,161) and server chunk (server:129) are both 4096 B, so both change together. Make the server's paced chunk a per-connection value announced in `OKAY` (`OKAY` + 2-byte window/1 KiB) and have the client use it; test 4/8/16/32 KiB. If throughput scales with window (RTT-bound, REVIEW §5.7) this alone can give 2–4× at no format change. **Compatibility**: old clients get 4096 as today. **Rollback**: constant.

**E6 SFU3 — resumable, cumulative-ACK upload.**
```
C→S  "SFU3" + token(64)                     S→C  "OKAY"
C→S  !H name_len, !Q size, 32B sha256, name  S→C  "RSME" + !Q offset   (0 if new; >0 if a verified .part prefix exists)
C→S  body from <offset>, any pipelining       S→C  "ACKO" + !Q offset  every 32 KiB (cumulative, durable on VM)
C→S  (end)                                    S→C  "DONE" + sha256     (after hash+CSV validation+publish, as today)
```
Server keeps `.part-<sha256(name|size|digest)>`, fsyncs per ACK, re-hashes the existing prefix at resume (cheap on the VM), validates CSV only on the finished file, deletes `.part` older than 7 days, caps `.part` total to the existing disk reserve. Publication stays temp→fsync→exclusive link→dir fsync; `SFU1/SFU2` stay accepted until retired. The client no longer pauses per 4 KiB: TCP provides flow control, the `ACKO` offsets only mark resume points. A stalled attempt now loses ≤ 32 KiB instead of the whole file. **Rollback**: ESP32 falls back to SFU2 (`#define`); server keeps both. **Verification**: extend `work/test_hourly_server.py` (interrupt at every offset class, restart server mid-upload, duplicate/conflicting resume, `.part` tamper), then HT-10.

**E7 CSV-preserving compact transport (CCT)** — the structural fix for F1/F3 without abandoning CSV.
- *Idea*: the SD keeps the CSV exactly as today (durable, human-readable, requirement preserved). During upload the ESP32 parses its **own** CSV rows (25 rows = one batch share `timestamp, uptime, device, boot, batch, buffer_count`) and sends per batch: `ts(25 B ASCII) + uptime u32/u64 + dev u8 + boot u64 + batch u32 + buffer u16 + 25 × {ax,ay,az,gx,gy,gz int16; acquired_us u64; sequence u32}` ≈ 26 B/row. The server regenerates the **byte-identical CSV** (gyro dps columns are `raw×0.07`, formatted `%.2f`) and compares the SHA-256 of the regenerated text with the digest the client sends. Any mismatch ⇒ reject; client retries the file in raw mode (SFU3 raw). Integrity is therefore end-to-end on the original CSV bytes.
- *Evidence it is feasible*: `review_tests/capacity/binary_to_csv_roundtrip.py` regenerates the real `CsvStore` output byte-for-byte from packed 617-byte batches and confirms Python `%.2f` = C `printf %.2f` for all 65 536 raw gyro values on the host (ESP32 newlib float printf is **not** yet tested — the digest check makes any difference fail-safe, and HT adds a real-device check).
- *Gain*: 118.5 → ≈ 26 B/row = **≈ 4.5×** fewer bytes (8.89 → ≈ 1.95 kB/s); even at the measured 5.5 kB/s upload this is 2.8× headroom; the pre-hash disappears if the digest sidecar (E3) is used and the hash is verified in one pass.
- *Costs/trade-offs*: ESP32 CSV parser (~60 lines, integer parsing only), server generator (~60 lines), more test surface; SD still fills at 768 MB/day (E4e/E10-class mitigation or a larger/endurance card remains necessary). *Alternative considered*: a parallel **binary** file on the SD (4.7× smaller *storage* too) — rejected as default because it changes the SD artifact the brief asked for and adds a second fsync stream (+≈ 1 commit per batch); keep as option if SD capacity, not bandwidth, becomes the limit. *Alternative considered*: deflate on the ESP32 — no compressor in ROM (only `tinfl`), a software deflate needs a window + tables that this heap cannot spare during TLS; server-side compression is irrelevant to the uplink.
- *Real-data caveat*: zlib/xz ratios (3.9–8.2× on synthetic data) are *not* evidence for real sheep data; the CCT gain does not depend on data entropy (it only removes text overhead).
- **Rollback**: switch client to raw mode; server keeps raw path. **Verification**: byte-exact regeneration test against the real firmware output (add an on-device dump of one hour to the test), fuzz the parser with truncated/odd CSV, then HT-10.

**E8 BLE tuning from the experiments** (only after HT-04/05): adopt the winning interval profile; `deleteAttributes=false` + cache GATT after first discovery (the collar GATT is static; saves ≈ 1–2 s of air time and heap churn per reconnect — re-discover only when `getService` fails); log-and-test `setDataLen(251)`; absent-collar back-off (E1 P3). **Rollback**: constants. **Verification**: HT-02 (4 h, 0 × 0x208, 0 overflow), reconnect p95 < 15 s.

**E9 Server hardening and monitoring (VM; owner approval per item).**
1. Take `busy` only **after** a valid token and shorten the pre-auth read to ≈5 s (`sheep_file_server.py:89-96`); re-run `review_tests/server/repro_busy_slot_dos.py` (must show legit success) and `work/test_hourly_server.py` (10/10). 2. Optional mTLS (`ssl.CERT_REQUIRED` + client cert on the ESP32; +≈ 3 kB heap — evaluate against F6). 3. Remove SFU1 once no gateway uses it. 4. Plausibility checks on file names (date window) and a daily upload quota. 5. `.part` cleanup, structured log lines, `last_upload` heartbeat file. 6. `check_cloud.py`: print `openssl`-style `notAfter` of the server certificate, warn < 60 days; cron/alerting for "no new file in 2 h" and "disk < 20 %". 7. systemd extras: `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, `ProtectKernelTunables/Modules/ControlGroups`, `SystemCallFilter=@system-service`, `LockPersonality`. 8. Scheduled disk snapshots (owner/cloud decision). Token: introduce per-gateway tokens (server accepts a small file of tokens), keep rotation runbook. Certificate renewal: re-issue the server cert from the same CA ≥ 60 days before its end date — **no firmware change** (CA valid to 2036-09-23). **Rollback**: previous unit/script kept as `*.pre-eN`. **Verification**: repro scripts + HT-11.

### Phase 3 — outage capacity

**E10 Enlarge the collar ring.** 1365 samples = 54.6 s is the hard limit for any ESP32 outage (SD fault restart, firmware crash, Wi-Fi/BLE storm). If the MG24 build map shows RAM headroom (*unverified*), raise `RING_CAPACITY` to ≈ 4000 (≈ 160 s, 96 kB). **Order matters**: first release a gateway that accepts `bufferCount` up to a larger bound (ino:229 currently rejects > 1365), then reflash collars one at a time. **Rollback**: gateway bound change is harmless; reflash old collar image. **Verification**: HT-08 (power-cycle ESP32 for 60/120/150 s, expect 0 overflow).

---

## 3. Compatibility and risk matrix

| Aspect | E0–E4 | E5/E6 | E7 | E8 | E9 | E10 |
|---|---|---|---|---|---|---|
| SD CSV format/name | unchanged | unchanged | **unchanged** | unchanged | — | unchanged |
| Checkpoint format | unchanged (E4a logic only) | unchanged | unchanged | unchanged | — | unchanged |
| Server CSV bytes | identical | identical | **identical (digest-checked)** | — | identical | identical |
| BLE protocol (v3) | unchanged | — | — | unchanged | — | `bufferCount` bound only |
| Wire protocol | unchanged | SFU2.1 (compatible) | SFU3 (additive) | — | SFU1 retired later | — |
| Old firmware still works against new server | yes | yes | yes | — | yes (until SFU1 retired) | — |
| New sidecars | `*.sha` | server `.part-*` | — | — | heartbeat file | — |

## 4. Hardware alternatives (decision, not default)

Decision inputs: HT-01 (TLS+Wi-Fi ceiling without BLE), HT-03/09 (heap/stall under BLE), `ss -ti` RTT.

| Option | What it fixes | Evidence it is needed | Costs/risks |
|---|---|---|---|
| **Software only (E2–E8)** | window/RTT bound, retry fairness, bytes on the wire, coexistence tuning | HT-10 passes: ≥ 20 kB/s mean (≥ 2.2× capture), ≥ 12 kB min free heap, 0 stalls in 24 h | none |
| **ESP32 with PSRAM (WROVER/S3-N8R8)** | TLS buffers ≥ 16 kB move off the 60 kB internal heap (large allocations fall to PSRAM when enabled; *must be verified per build*) | min free heap < 12 kB or allocator-related stalls remain after E2/E8 | new board bring-up; same single 2.4 GHz radio; pins 16/17 reserved on WROVER (SD pins unaffected) |
| **LAN-side uploader** (Pi/mini-PC pulls sealed files over the LAN; it owns TLS + WAN) | removes WAN RTT (~0.2 s) from the ESP32 stop-and-wait, removes TLS from the BLE/SD device, resumable HTTP for free | HT-01 shows rate is RTT/window-bound and E5/E6 cannot reach target; or heap remains the blocker | needs an authenticated LAN channel (token + TLS with LAN CA, or PSK) — plaintext LAN is a security trade-off the owner must accept explicitly; one more device to power/monitor |
| **Ethernet (W5500/LAN8720)** | removes Wi-Fi/BLE time-slicing | HT-01 ≫ HT-03 throughput and E2 core separation insufficient | wiring on site |
| **Endurance/industrial microSD** | wear from ≈ 3 sync writes/batch, commit-time tail | max commit time keeps growing (log 08: 1.18 → 1.52 s) or SD faults | cost only |
| **RTC (DS3231)** | wall-clock before SNTP, fewer `unsynced_*` files | frequent boots without Wi-Fi | one I²C part |
| Dual-ESP32 (BLE+SD / uploader) | isolates BLE from TLS | not supported by evidence — no shared-SD solution without a second durable copy | complexity; not recommended |

## 5. What would change this plan

- HT-01 shows ≥ 20 kB/s with no stalls and HT-03 stalls return ⇒ coexistence is the blocker: prioritise E2/E8 and the Ethernet/LAN-uploader options over protocol work.
- HT-01 itself stalls ⇒ the path (loss/MTU/heap) is the blocker: pcap-driven fix, E6 (resume) first, PSRAM/LAN-uploader earlier.
- HT-04 shows uniform intervals fix Sheep-03 ⇒ adopt P1/P2 and close F5; if not, escalate to collar swap/RF (HT-04c) before any BLE redesign.
- A real hour of CSV compresses far better/worse than the synthetic range — irrelevant to E7 (text-overhead removal), relevant only if compression is added later.
