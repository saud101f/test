# Controlled hardware test plan

Companion to [`REVIEW.md`](REVIEW.md) and [`ENHANCEMENT_PLAN.md`](ENHANCEMENT_PLAN.md). **None of these tests has been run.** They are written so each answers one question with numeric pass/fail criteria. Tests marked *(v1.3 as shipped)* need no new firmware; tests marked *(E0)* need the instrumentation build.

## 0. Rules for every test

1. **Preserve data.** Before the first test copy the SD card contents to the laptop (card reader, read-only copy), record `sha256sum` of every file, and keep the copy. Tests that fill or fault a card use a **scratch card**, never the production card. Nothing is deleted from SD or VM.
2. **No pooling.** Each run starts from a fresh power-up. Record per run: firmware build ID/hash of the ESP32 and of each MG24 (print or flash-readback), bootIDs, collar placement (photo/diagram, distance to ESP32, orientation), ESP32 position, Wi-Fi AP and RSSI, ambient, power source. A run with a manual restart is a new run.
3. **One variable at a time.** Change one factor between runs; randomise order of A/B runs; repeat any decisive result once.
4. **Time-align everything.** Capture serial with `review_tests/tools/serial_timestamp_logger.py` (host UTC stamp on every line). On the VM (owner-run, read-only) run `review_tests/tools/vm_ss_sampler.sh 8443` and `journalctl -u sheep-files -f`. Do not run `check_cloud.py` against production during an active upload (single-client admission).
5. **Owner approval** is required for anything on the VM, firewall, token, certificate or service. Packet captures use `-s 96` (headers only) and are deleted after analysis if the owner wishes.
6. **Abort criteria (all tests):** SD fault line, `connected_mask` stuck at 0 for >120 s with collars enabled, collar overflow counter rising on a test not designed to stress it, any sign of overheating/battery swelling. After an abort, copy the SD, then restore the standard configuration (`uploads off`, `collars 123`).
7. Commands in v1.3 (Newline required): `uploads on|off`, `collars 123|13|3|none`, `status`. Masks: 1=Sheep-01, 2=Sheep-02, 4=Sheep-03.

Standard post-processing: `python review_tests/evidence/evidence_analysis.py`-style summaries for logs; `python review_tests/tools/csv_continuity_check.py <folder>` on SD copies and on VM copies (duplicates, partial batches, gaps, regressions).

## 1. Test matrix

| ID | Question | Needs | Duration | Blocks |
|---|---|---|---|---|
| **HT-00** | What exactly is deployed? | v1.3 | 1 h | all |
| **HT-01** | What does Wi-Fi+TLS+SD achieve with **no BLE load**? *(single best next test)* | v1.3 | 1–2 h | E3/E5/E6/E7 priorities, hardware decision |
| HT-02 | Is three-collar BLE stable for hours (uploads off)? | v1.3 | 4 h | F5 |
| HT-03 | Does concurrent BLE+upload hold (current firmware)? | v1.3 (+E0) | 8 h | F4/F6 |
| HT-04 | Which factor fixes Sheep-03: pair, interval, core, hardware? | E1/E2 | 2 days | F5 |
| HT-05 | What does an absent collar's initiator cost? | v1.3 | 2 h | E1/E8 |
| HT-06 | SD commit tail, capacity alarm and full-card behaviour | E0 + scratch card | 24 h + 4 h | F3/F8 |
| HT-07 | Power-loss campaign | relay, scratch card | 1 day | durability |
| HT-08 | Outage capacity and recovery | v1.3/E4 | 3 h | ring/E10 |
| HT-09 | 72 h endurance | final build | 72 h | acceptance |
| HT-10 | Throughput/backlog acceptance after E3,E5–E7 | those builds | 2 days | F1 |
| HT-11 | Security & lifecycle checks (dry-run/test instance) | VM owner | 2 h | F7/F9 |

Suggested order: **HT-00 → HT-01 → HT-02 → (HT-04 if HT-02 fails) → HT-03 → HT-05 → E3/E4 builds → HT-06/07/08 → E5–E7 → HT-10 → HT-09 → HT-11 (in parallel with E9)**.

---

## HT-00 Baseline and identification *(v1.3)*

**Procedure.** (1) Copy SD (rule 1). (2) Record ESP32 build ID, `status`, free SD space, queue (`queued_files`), file sizes. (3) For each collar record the build/hash; if the 01/02 image cannot print its address, label boards physically and note which log came from which (log 12 is unlabeled in the evidence). (4) Owner (VM): `systemctl status sheep-files`, `df -h`, last 50 journal lines, `openssl x509 -in <server.crt> -noout -enddate -dates` (read-only), disk usage of `hourly-files`. (5) From the laptop on the same Wi-Fi: 200 pings (or `tcping`) to the VM address → min/avg/p95/loss; note mesh node/AP the ESP32 is on.
**Acceptance.** All items recorded with no gaps. Outputs feed RTT (REVIEW §5.7), certificate end date (F9) and SD/VM capacity days (F3).

## HT-01 TLS/Wi-Fi ceiling without BLE *(v1.3 as shipped)* — **single best next test**

**Why.** F1/F4: the upload is slower than capture and stalls after 2.5–3.5 kB; the unknown is whether BLE coexistence, the network path, or heap is responsible. With the collars off, BLE is removed completely; only Wi-Fi + TLS + SD remain. The result is the throughput ceiling the plan depends on and it needs no code change.

**Setup.** Collars **powered off** (not merely masked — nothing to overflow, nothing to lose). ESP32 powered normally; Serial logger running; `uploads off` is the boot default. VM sampler + journal running. Pre-staged queue: the 9.85 MB file that previously failed plus the 0.97 MB file (both on the SD copy). Optional: short `tcpdump -s 96 -w` on the VM for one attempt.
**Procedure.** Confirm `connected_mask=0` (use `collars none` to stop the connection attempts as well). Send `uploads on`. Record until the file verifies or 45 min pass (the 9.85 MB file needs ≈ 30 min at 5.5 kB/s). Then repeat once, then repeat with the 0.97 MB file. Finally `uploads off`, `collars 123`, power the collars back on.
**Measurements.** Bytes/10 s from `UPLOAD progress` (also first-minute vs later rate), stalls (progress unchanged > 15 s), `UPLOAD diagnostic` lines, heap min/largest per status line, VM `ss` samples (rtt, retrans, cwnd, mss, bytes_received), journal result (`Stored` or `Upload failed … received_bytes=`).

**Outcome → branch** (applies to the mean rate over the whole file):

| Outcome | Reading | Next |
|---|---|---|
| A. completes, ≥ 20 kB/s, 0 stalls | Wi-Fi/TLS/SD path is fine; the BLE load (or core/coex) is what limits it | HT-03 with E0; E2 (cores), E8 |
| B. completes, 8–20 kB/s, 0 stalls | RTT/window-bound (compare `rtt` and `4096/(rtt+0.04)`): protocol, not radio | E5 → E6/E7; consider LAN-side uploader |
| C. completes, < 8 kB/s or decaying | heap/allocator or loss; look at heap trend and `retrans` | E2c guard, E6, PSRAM decision |
| D. stalls/fails like before (≈ 22 s after "Receiving", 2.5–3.5 kB) | path or ESP32 TCP problem independent of BLE | pcap on VM + ESP32 `SO_ERROR`/timing (E0); MTU/loss checks; E6 |

Interpretation aids: `retrans > 0` with high `rtt` ⇒ loss; `bytes_received` flat while ESP32 reports progress ⇒ data stuck in the ESP32 TCP queue (heap/Wi-Fi); `rcv_space`/`cwnd` small and flat with no retransmits ⇒ sender not offering data (task/SD stall).
**Acceptance (test validity).** Two complete runs, logs time-aligned, VM samples present. The *outcome* is the result; there is no pass/fail on the rate itself.

## HT-02 Three-collar BLE baseline, uploads off *(v1.3)*

**Setup.** Final placement of all three collars, uploads off, 4 h, Serial logger.
**Measurements.** Per link: `BLE DIAG disconnects`, reason codes, link-up durations, `BLE PARAM`, `invalid`, notifications/s; ESP32: `saved_batches` rate (should be 3/s), `max_save_ms`; collar: overflow, `buffer` max.
**Acceptance (PASS).** 0 × reason 520 (0x208); 0 overflow on all collars; `buffer_count` ≤ 75 in > 99 % of batches (rare bursts to 38 seen in log 12 are acceptable if they drain); `saved_batches` ≥ 99.9 % of 3/s × duration; `max_save_ms` < 1500; stack min-free ≥ 1000 B on all tasks.
**Fail.** Any 0x208 ⇒ HT-04.

## HT-03 Concurrent BLE + upload, current firmware *(v1.3, E0 preferred)*

**Setup.** All three collars, `uploads on`, ≥ 8 h or until the backlog of ≥ 3 files verifies; VM sampler/journal running; start from a fresh boot.
**Measurements.** Upload kB/s per file (10 s bins), stall events, `UPLOAD verified` count, link drops, overflow, heap min/largest every 5 s, SD commit histogram (E0), `ss` series.
**Acceptance (current firmware, informational).** All three queued files verified; 0 × 0x208; 0 overflow; min free heap ≥ 6 kB (current worst case 6 248); largest block ≥ 1 kB. **Target for the improved build:** min free ≥ 12 kB, largest ≥ 4 kB, 0 stalls/8 h.
**Compare with HT-01**: rate_with_BLE / rate_without_BLE quantifies the coexistence cost.

## HT-04 What fixes Sheep-03? *(E1/E2 builds)*

Randomise order; 1 h per cell, repeat each failing/borderline cell once; uploads off (so TLS cannot confound), then repeat the best cell with uploads on.
| Cell | Variable | Predicts if H1 (harmonic/scheduling) true |
|---|---|---|
| a1 | pair 01+03 only (`collars 13`) at P0 | fails |
| a2 | pair 02+03 | less likely to fail (480 ms commensurate) |
| a3 | pair 01+02 | stable |
| a4 | 03 alone (`collars 3`) | stable (log 03) |
| b1 | all three, P0 (80/120/160) | fails (control) |
| b2 | all three, P1 (all 80 ms) | stable |
| b3 | all three, P2 (all 100 ms) | stable |
| c1 | all three, P0 + upload/host tasks on core 1 (E2a) | tests coexistence/scheduling hypothesis |
| c2 | c1 + `CONFIG_BT_NIMBLE_PINNED_TO_CORE 1` | idem |
| d1 | swap boards: ID-03 image on the board that was 01 and vice versa; same placements | if failure follows the *position/ID* ⇒ gateway-side; if it follows the *board* ⇒ collar RF/hardware |
| d2 | move Sheep-03 to Sheep-01's exact position (same board) | placement/RF vs board |
**Measurements.** Per link: 0x208 count/h, link-up durations, connect time, RSSI (E0), PHY/interval, and on the collar the disconnect reason.
**Acceptance.** A cell is "stable" if 0 × 0x208 and 0 overflow for 1 h; promote it to HT-02 (4 h). The deliverable is the identification of the **factor** (pair/interval/core/board/position); a single stable hour on the failing control (b1) must not be over-interpreted — the earlier flap cycle was ≈ 13 s, so a failing configuration shows itself within minutes.

## HT-05 Absent-collar initiator cost *(v1.3)*

Collars 01 and 03 on, 02 physically off. (i) `collars 123` (ESP32 keeps trying to connect to 02: 15 s on / 2 s off, 20 ms scan windows) for 30 min with uploads on; (ii) `collars 13` for 30 min with uploads on. **Measurements:** 03/01 link drops, upload progress rate, stalls. **Acceptance:** (ii) has 0 drops; the delta (i)−(ii) in drop count and kB/s is the initiator cost. If significant ⇒ E1 P3 (reduced scan duty + back-off), then verify reconnect p95 < 15 s on a collar power-cycle (HT-08).

## HT-06 SD commit tail, capacity and full-card behaviour *(E0, scratch card)*

(a) 24 h normal operation, production-like: commit histogram → **p50, p99, max**. Acceptance: p99 < 300 ms, max < 1500 ms, no growth trend (log 08 shows `max_save_ms` 1.18 s → 1.52 s over hours; if it keeps growing, test an endurance card). (b) Pre-fill a **scratch** card to 80 % with dummy files, run until alarm and fault: alarm must appear at the threshold (E4d); at full the gateway must stop ACKing, report SD fault visibly (LED/serial/heartbeat), restart per policy without corrupting the checkpoint; after freeing space manually it must resume (checkpoint valid). (c) With 1 000 small files on the scratch card measure `chooseFile()` duration and the induced `max_save_ms`; acceptance < 200 ms or sliced scanning.

## HT-07 Power-loss campaign *(relay + scratch card first, then production copy)*

Relay-cut ESP32+SD power at pseudo-random times while three collars stream and files rotate (include ≥ 10 cuts within ±30 s of an hour boundary and ≥ 10 within 3 s of a power-up, plus 10 cuts on a **blank** card during first boot). Each cut lasts 5–30 s (< 54 s so collars retain data). After each recovery copy the SD (read-only) and run `csv_continuity_check.py`; after the whole campaign run `fsck.vfat -n` on an image of the card.
**Acceptance.** 50/50 cycles: boots without "no valid checkpoint" (after E4a; before E4a expect only blank-card cuts at risk), 0 duplicate batches, 0 partial batches, 0 missing batches (`samples_missing == 0` for every collar and every cycle), first valid ACK < 20 s after power-up (p95), `fsck` reports no corruption beyond the dirty flag. Any loss/duplicate of an **ACKed** batch is a P0 and stops the programme.
*This is the only test that exercises real FAT/card behaviour; the host crash matrix cannot.*

## HT-08 Outage capacity and recovery *(v1.3 / E4)*

(a) Hold the ESP32 in reset (EN) for 30, 45, 60, 90 s: record per-collar max `buffer`, overflow, time to first ACK, time to drain. Expectation from the ring size: no overflow ≤ 45 s, overflow from ≈ 55 s; drain ≈ 0.32 s per batch per collar when alone. (b) AP off 10 min: collection unaffected, uploads fail, resume afterwards. (c) VM `systemctl stop sheep-files` 10 min then start (owner): ESP32 retries with backoff, later success. (d) VM `systemctl restart` during an upload: with SFU2 the file restarts from 0; with E6 it resumes (HT-10).
**Acceptance.** (a) 0 overflow for 45 s outages and a *measured* limit documented; first ACK < 20 s after boot; drain time ≤ 60 s per collar. (b)(c) 0 collar impact; upload resumes ≤ 2 retry intervals.

## HT-09 72 h endurance *(final build)*

All three collars, uploads on, normal placement. Sample every 5 min: free/largest heap and **minimum watermark**, stack min-free, commit p99, link drops, queue length/oldest age, upload kB/s, SD free, VM disk. Run `csv_continuity_check.py` on the VM copies of every hour file and compare SHA-256 with the SD files.
**Acceptance.** 0 × 0x208, 0 overflow; zero unplanned reboots; queue age < 2 h in steady state (backlog not growing, i.e. upload mean ≥ 1.5 × capture over any 6 h window); heap min ≥ 12 kB and largest ≥ 4 kB; stacks ≥ 1 kB; all hour files verified in cloud with 0 duplicate/partial batches; SD free decreases only as predicted (≈ 768 MB/day for raw CSV).

## HT-10 Throughput/backlog acceptance *(after E3, E5–E7)*

(a) A full-hour (≈ 32 MB) file uploads end-to-end at ≥ 20 kB/s mean with 3 collars live. (b) A 15-file backlog drains while collecting: queue non-increasing over 24 h and ≤ 3 files after 24 h. (c) Interrupt tests: drop Wi-Fi at ≈ 10/50/90 % of a file; with SFU3 the retry resumes with ≥ 90 % of already-acknowledged bytes preserved; with the blocked-file scenario the other files still verify (F2). (d) CCT byte-exactness: for 5 hour files `sha256(server CSV) == sha256(SD CSV)`; with an injected fault (flip one CSV byte on a scratch copy) the upload must be rejected and fall back to raw.
**Acceptance.** All four; plus min free heap ≥ 12 kB, 0 stalls, 0 × 0x208 during the run.

## HT-11 Security and lifecycle checks *(owner-approved; test instance where stated)*

1. Certificate end date read (HT-00) and a calendar alert 60 days before; renewal rehearsal on a **test instance** (new server cert from the same CA, service restart) — a gateway must still connect with no firmware change.
2. Negative TLS tests on a test instance: gateway pointed at a server with an unknown CA, an expired certificate, and a wrong IP SAN must each **fail verification and upload nothing** (confirms `VERIFY_REQUIRED`).
3. Wrong token ⇒ `FAIL`, no file; correct token only ⇒ upload.
4. Pre-auth slot: run `review_tests/server/repro_busy_slot_dos.py` against the **hardened** test instance (after E9) — legit authentication must succeed while junk TLS connections are held.
5. Token rotation dry-run on the test instance (two tokens accepted during overlap).
6. Log review: no token/SSID/password in any serial or journal log.
**Acceptance.** All six; findings recorded with dates.

---

## 2. Data package to return after each test

Timestamped serial logs, VM `ss`/journal excerpts, SD file listing with sizes + SHA-256, `csv_continuity_check` output, photos/notes of placement, build IDs, a one-line statement of what changed since the previous run. With these, every claim in REVIEW.md that is currently "Hypothesis" or "Unknown" can be upgraded or retired.
