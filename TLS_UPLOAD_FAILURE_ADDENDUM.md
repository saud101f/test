# TLS upload failures — addendum for evidence 13 (supersedes the TLS parts of REVIEW F1/F4)

Input: `Sheep_Claude_Review_2026-10-01.zip` (second delivery; sources identical to the first, plus `evidence/13_v13_upload_failures_ble_stable.txt`, START_HERE addendum, CLAUDE_PROMPT update). Review only — no firmware, server, VM or settings were changed. Re-derive every number with `review_tests/evidence/tls_failure_analysis.py <bundle_root>`.

Confidence labels: **Proven** (source/primary docs + logs), **Likely** (fits all evidence, not yet excluded by a decisive measurement), **Hypothesis**, **Unknown**. Primary sources: arduino-esp32 3.3.11 (`NetworkClientSecure.cpp`, `ssl_client.cpp`), ESP-IDF v5.5 `lwipopts.h`, esp-lwip `api_msg.c`/`tcp_priv.h`, arduino-esp32 lib-builder `configs/defconfig.common` (**master branch — not necessarily the exact configuration of the installed 3.3.11 libraries; see "To verify" below**).

## 1. Executive summary

1. **Evidence 13 changes the picture.** For ≈ 5 400 s (90 min) all three collars had **0 disconnects, 0 invalid notifications, 25-sample buffers**, at the same 80/120/160 ms intervals as the earlier failing runs — and the upload **still failed 23 times, 0 verified, 17 files queued**. BLE instability is therefore **not required** to make the TLS transfer fail, and my earlier statement that Sheep-03 flapping "raises the stall hazard" is withdrawn as an explanation for the TLS failures.
2. **A new invariant.** All 15 failure events in this excerpt (plus the 35 server-side failures of log 09) happen **5, 6 or 7 records (2560/3072/3584 B) into a 4096-byte window**, *always* at `stage=body`, *never* while waiting for `MORE`, including the ones that fail after 1.0 MB and 1.46 MB. `sent_bytes mod 4096 ∈ {2560, 3072, 3584}` in 15/15.
3. **Leading explanation (Likely): memory starvation of the network stack during TLS.** In this firmware every lwIP buffer comes from the same 8-bit heap (`MEM_LIBC_MALLOC=1`, `MEMP_MEM_MALLOC=1`), TLS leaves 4.9–11.7 kB free (median) with a **largest free block of only 0.45–3.8 kB (median 3.8 kB)**, and ~2 full TCP segments (≈ 2.9 kB, i.e. 5.3 records) is all the heap can hold before `tcp_write` runs out of memory. In lwIP a non-blocking `tcp_write` that hits `ERR_MEM` returns `ERR_WOULDBLOCK` → `errno=EAGAIN (11)` → mbedTLS `WANT_WRITE` → the core loops silently until its **15 s no-progress timeout** and closes. That is exactly the logged signature (`errno=11`, no `_handle_error` line, 15–25 s after the last progress print).
4. **Loss / Wi-Fi-airtime explanations are now much less likely *for the write failures***: with the default send buffer (5 744 B) a 4 328-byte window can never fill the socket buffer, so packet loss alone would make the ESP32 finish writing the window and then wait for `MORE` (`stage=window-ack`) — which never happens in 50 observed failures. (They can still explain the *slow rate*, §5.)
5. **The throughput ceiling and the failure hazard multiply.** Observed mean rates were 0.6–4.4 kB/s per attempt (log 13) against 8.89 kB/s generated, and with a per-window stall hazard of the observed order (≈ 1–2 %) a file of N windows completes with probability ≈ e^(−0.02·N): ≈ 1 % for the 968 kB file, **≈ 10⁻²⁰ for the 9.85 MB file, effectively impossible for a 32 MB hour file**. *Without resumable chunks no amount of retrying will drain the queue.*
6. **Recommended order (nothing applied):** (T0) instrument the failure instant, (T1) resumable chunked upload (turns a failure into a ≤ 2 KiB loss instead of a restart), (T2) bound the bytes in flight to what the heap supports (window 4096 → 2048/1024, heap guard) and set `TCP_NODELAY`, (T3) cache the digest and add retry fairness, (T4) free heap or move TLS buffers off the internal heap (PSRAM module), (T5) cut bytes on the wire with the CSV-preserving compact transport.
7. **Single best next test (replaces HT-01 as #1): HT-12** — an instrumented A/B of window size and heap at the failure instant (needs the owner's approval to flash an instrumented build). HT-01 (collars off) is demoted: under both leading hypotheses it would be expected to fail again, so it adds little.

## 2. What evidence 13 contains (and does not)

| Item | Value | Note |
|---|---|---|
| Gateway uptime at end | ≈ 5 426 s; `saved_batches=16338`, `uploaded_files=0`, `upload_errors=23`, `queued_files=17` | counters are since reboot; "0 uploaded" does not erase the earlier verified file |
| BLE | `disconnects=0 invalid=0` on all links for the whole excerpt; negotiated 80/120/160 ms, timeout 4 s; RSSI −71…−40 dBm | not an endurance proof (90 min), but TLS failed throughout |
| Failure events present | 15 (`op=write-failed stage=body errno=11`); 23 counted by the firmware | the log is **excerpted**: several attempts have no failure line (e.g. the 2.52 MB attempt) — do not treat the list as complete |
| `sent_bytes` at failure | 2560×3, 3072×3, 3584, 7680, 15 872, 24 064, 32 256, 36 352, 39 936, **1 014 272**, **1 461 248** | all ≡ 2560/3072/3584 (mod 4096); all ≡ 0 (mod 512) |
| Time from last `UPLOAD progress` print to failure | 16.4, 16.7, 17.5, 18.7 s | print period ≥ 10 s + 15 s no-progress timeout ⇒ 15–25 s; stall began within ≈ 1–2 s of the print |
| Per-attempt mean rates (10 s bins) | 3.7, 3.6, **1.0**, 1.9, 2.4 kB/s; best bin 5.7 kB/s | earlier v1.3 run: 5.5 kB/s mean, 8.6 → 4.1 |
| Heap sampled every 5 s while `ACTIVE` (n = 618) | free min 4 912 / median 11 680 B; **largest block min 452 / median 3 828 B**; min seen "7768/756" matches | status lines are 5 s samples, not the failure instant |
| Heap printed with the failure | ≈ 45–49 kB, largest 34.8 kB | **printed after `stop()` freed the TLS context** (the core closes the client before the caller logs) — it says nothing about the failure instant, as the handoff warns |
| `errno=11` | 15/15 | `EAGAIN`; see §3.3 on why it is consistent with, but not proof of, `ERR_MEM` |
| Handshake→body | hash ≈ 35 s, `TLS begin` heap 56.7–57.0 kB / 43 kB largest | every retry repeats the 35 s hash and restarts at byte 0 |
| Not present | server journal for this run, TCP/RTT data, `UPLOAD verified` | the single most valuable missing item is the VM journal + `ss -ti` for the same window |

## 3. Mechanism analysis (pinned code paths)

### 3.1 Where "write failed" comes from — Proven
`NetworkClientSecure::write()` closes the client when `send_ssl_data()` returns <0 (`NetworkClientSecure.cpp:248`, `stop()` right after, so the TLS context is freed *before* `cloudWrite` prints heap — HR:82-93). `send_ssl_data()` (`ssl_client.cpp:431-467`) loops: on `ret > 0` it advances; on `WANT_READ/WANT_WRITE` it `vTaskDelay(2)` and retries; it returns −1 if **`millis() − last_progress > ssl_client->socket_timeout`** (checked first, `:453`, logged only at *verbose*), otherwise real errors go through `handle_error()` which always logs (`:41`). Log 13 has 15 "Closing connection on failed write" and no `_handle_error` ⇒ the 15 s no-progress path. `socket_timeout` = the `connect()` timeout argument, **15 000 ms** (`NetworkClientSecure.cpp:129-130` → `ssl_client.cpp:116`); `client.setTimeout(30000)` is `Stream::setTimeout` and unused (REVIEW §5.3). *Peer close-notify also returns −1 silently, so a few of the 15 could be server-side closes; the server log of the same run would settle it.*

### 3.2 What can make a non-blocking write return "would block" for 15 s
`mbedtls_ssl_write → mbedtls_net_send → lwip_send` on an `O_NONBLOCK` socket (`ssl_client.cpp:91`) returns EAGAIN when lwIP's `lwip_netconn_do_writemore` sees `ERR_MEM` or no send-buffer space and `dontblock` is set (`api_msg.c` ≈ L1757-1775: *"non-blocking write is done on ERR_MEM, set error … `ERR_WOULDBLOCK`"*). Candidate causes:

| # | Cause | Predicts at `stage` | Consistent with the 5/6/7-record invariant? |
|---|---|---|---|
| a | **send buffer full** (`TCP_SND_BUF` 5 744 B in the lib-builder defaults; *verify in the installed core*) | `body`, after ≥ 5.7 kB unacked | **No**: a window is ≤ 4 328 wire bytes and `MORE` proves all earlier data was ACKed, so the buffer can never fill (unless the installed build sets a much smaller buffer — see "To verify") |
| b | **heap exhaustion in `tcp_write` (`ERR_MEM`)** — pbufs/segments are `malloc`ed (`MEM_LIBC_MALLOC=1`, `MEMP_MEM_MALLOC=1`, IDF `lwipopts.h`); with `LWIP_TCP_OVERSIZE_MSS` each new segment wants ≈ 1.4–1.5 kB **contiguous** | `body`, after ≈ 2 segments | **Yes**: 5/6/7 records = 2 705/3 246/3 787 wire bytes = 1.9/2.3/2.6 MSS (1 436 B); largest free block median 3.8 kB but as low as 452 B |
| c | Wi-Fi driver TX buffers (dynamic, one heap allocation per frame; ≈ 1.6 kB; IDF Kconfig default) can't be allocated → segment queued but never leaves; the ACK that would free pbufs never arrives; retransmit needs the same allocation (RTO initial **3 000 ms**, doubling) | first `body` write that needs memory, then `body` | **Yes** — and it explains why a stall can last > 15 s once started (livelock: the memory is held by the very data waiting to be ACKed) |
| d | packet loss / airtime starvation / MTU black hole | `window-ack` (writes complete into the buffer, `MORE` never arrives) | **No** for the failures (0 of 50 at `window-ack`); **Yes** for slow rate |
| e | server-side backpressure | `body` after the server stops reading | No: server reads each 4 KiB chunk immediately (server:129-136), and 0 of 50 failures follow a missing `MORE` |
| f | TLS 1.3/renegotiation `WANT_READ` inside `mbedtls_ssl_write` | any | Not excluded by logs (negotiated version/cipher are never printed) but would not produce a window-position invariant |

So **b+c (memory)** is the only candidate that explains the *position* invariant, the `errno=11`, the absence of mbedTLS error logs, the >15 s duration, and the early-connection clustering (7 of 15 failures occur in the first window, when TLS has just consumed ≈ 49 kB; log 09: 35/40 within two windows). *Likely, not proven*: the heap at the failure instant has never been captured.

### 3.3 About `errno=11`
It is what an `ERR_MEM`→`ERR_WOULDBLOCK` write produces, but `EAGAIN` is also what every idle non-blocking `recv` leaves behind (the `MORE` polling in `cloudRead`, HR:72-81, calls `available()` every 5 ms), and the core's `stop()` runs before the diagnostic prints — so `errno=11` is **supportive, not decisive**. The `last_error_hint=48` is the socket fd (REVIEW §5.1).

### 3.4 Why TLS costs ≈ 49 kB
`CONFIG_MBEDTLS_ASYMMETRIC_CONTENT_LEN` is **not set** in the lib-builder defaults, so mbedTLS allocates a full 16 kB input *and* output record buffer (≈ 33 kB) plus context/CA/handshake state; `largest_free` 43 kB → 0.45–3.8 kB matches. The 512-byte application write size does **not** reduce this (buffers are sized at setup).

### 3.5 Throughput ceiling and rate decay (separate from the stall)
- lwIP Nagle (`tcp_priv.h:100-106`) sends a sub-MSS segment only when nothing is unacknowledged or `TF_NODELAY` is set. The 541-byte records therefore leave as: first record alone, the following ones coalesce into full segments, the **tail of each window (< MSS) waits one RTT for the ACK**, then the server's `MORE` costs another RTT ⇒ ≈ 2 RTT per 4 KiB. At RTT ≈ 0.24 s that is ≈ 8.5 kB/s — equal to the best 10 s bin ever seen (8.6 kB/s). `client.setNoDelay(true)` (public in `NetworkClient.h`) should remove one RTT. *Hypothesis; RTT is not measured.*
- Rates of 0.6–3.7 kB/s per attempt imply ≈ 1–6 s per 4 KiB window, i.e. repeated **RTO-length hiccups** (lib-builder `CONFIG_LWIP_TCP_RTO_TIME=3000`) inside otherwise successful attempts — the same memory/TX-buffer starvation or Wi-Fi loss, recovered by retransmission. The mean rate shows no correlation with the sampled largest-block (rate 3.6 vs 4.3 kB/s for ≤ 1.3 kB vs > 1.3 kB blocks, attempt 7) — expected if starvation is brief and the 5 s samples miss it.

### 3.6 Hazard model (why retrying cannot work)
Counting only observed events (15 failures; 647 windows completed before failure + 68 censored): ≈ 2 % per window; early-connection hazard higher. Success probability for a file of N windows ≈ e^(−0.02 N): 968 kB (236 windows) ≈ 1 % (the one success in 11 attempts of log 01 is the same order), 9.85 MB (2 405) ≈ 10⁻²⁰, 32 MB (7 800) ≈ 0. Even a 50 % smaller hazard leaves multi-MB files unreachable. **Resume granularity, not the root cause, decides whether the system works at all in the short term.**

## 4. Proposed changes (nothing applied) — ordered, each reversible

| Step | Change | Files | Why / predicted effect | Rollback |
|---|---|---|---|---|
| **T0** | **Instrument the failure instant.** Before every 512-B write record `largest_free`/`free` (cheap `heap_caps_*`) into a small ring; poll writability with `select()` on `client.fd()`; if the socket stays unwritable > 3 s print ring + `heap_caps_get_minimum_free_size()` + `SO_ERROR` **before** the core's own 15 s `stop()` (e.g. by calling `client.stop()` ourselves after logging, at 8 s); print TLS version/cipher (`mbedtls_ssl_get_version`) and heap before/after `connect()` and at `SEND`; print window index. Drop the `last_error_hint` line. Runtime commands `window <512–8192>` and `nodelay on/off`, `writechunk <n>` so variants need no reflash. | `HourlyRuntime.h`, `Diagnostics.h` | turns "Likely" into Proven/Refuted for H-b/c in one run | reflash v1.3 |
| **T1** | **Resumable chunked upload (SFU3).** Server keeps `.part`, answers `RSME offset`, acks cumulatively every 2–4 KiB; client resumes from the acked offset; digest sidecar so reconnect ≈ 3 s instead of 35 s hash + restart; lower the connect/no-progress timeout to ≈ 6 s once resume exists (a stall then costs ≈ 6 s + reconnect). Whole-file SHA-256 + CSV validation + atomic publish unchanged. | ESP32 + `sheep_file_server.py` (new magic, SFU1/2 kept) | converts hazard ≈ 2 %/window from "file lost" to ≈ 10 s per ≈ 50 windows (≈ 5 % overhead) — **the change that makes completion certain** | client falls back to SFU2 |
| **T2** | **Bound in-flight bytes + disable Nagle.** Window 4096 → 2048 (then 1024) on both sides (`HR:155,161`, `server:129`); wait until `largest_free ≥ 3 kB` before each record (cap wait 2 s); `setNoDelay(true)`. Predicts: failures vanish if memory-bound; rate ≈ 2048/(RTT+…) ≈ 7–8 kB/s with NoDelay. | `HourlyRuntime.h`, server constant | A/B in HT-12; also a *test* of the hypothesis | constants |
| **T3** | **Digest sidecar + retry fairness** (ENHANCEMENT E3): no per-attempt 35 s hash; per-file backoff so the 9.85 MB file stops starving 16 others; small files finish first. | `HourlyRuntime.h` | the 0.97 MB-class files get through meanwhile; retries cheaper | delete sidecars/revert |
| **T4** | **Give the network stack headroom.** (a) Free internal heap: drop `SD.begin` `max_files` 3→2 by opening the upload file per read inside `sdMutex` (−4 kB), keep GATT cache across reconnects (`deleteAttributes=false`), trim collar task stacks only where min-free > 1.5 kB (they are 1.5 kB → leave) — expected gain ≤ 8 kB, *not enough alone*; (b) **PSRAM module (ESP32-WROVER/S3-N8R8)**: with `SPIRAM_MALLOC_ALWAYSINTERNAL=4096` (lib-builder default) the two 16 kB TLS buffers (≈ 33 kB) move to PSRAM, leaving ≈ 40 kB internal for lwIP/Wi-Fi — the robust fix; (c) custom IDF/PlatformIO build with `MBEDTLS_ASYMMETRIC_CONTENT_LEN` (saves ≈ 12 kB), `LWIP_TCP_OVERSIZE_DISABLE`, static Wi-Fi TX buffers. | board / build | removes the cause rather than bounding it | revert board/build |
| **T5** | **CSV-preserving compact transport (ENHANCEMENT E7)**: ≈ 4.5× fewer bytes ⇒ ≈ 4.5× fewer windows per file ⇒ hazard exposure per file drops 4.5× and the rate requirement falls to ≈ 2 kB/s (inside what the link achieves even today). | both | with T1 makes the backlog drainable | raw mode |
| T6 | Server-side visibility: log TLS version/cipher, bytes/s per connection and `ss -ti` snapshot at failure; keep SFU1/2 until T1 proven. | server | settles close-notify vs client give-up | revert |

**Do not**: relax certificate verification, extend the 15 s timeout (it only delays the same failure), or raise the window (it increases in-flight memory).

### Minimal code sketches (for review only)
```cpp
// T0: pre-write ring + abort-before-core-stop (HourlyRuntime.h, inside cloudWrite)
struct HeapSample { uint32_t ms, free, largest; };           // 32-entry ring
static HeapSample ring[32]; static uint8_t ringPos;
static bool socketWritable(NetworkClientSecure &c) {          // select() with 0 timeout
  fd_set w; FD_ZERO(&w); FD_SET(c.fd(), &w); timeval tv = {0, 0};
  return select(c.fd()+1, nullptr, &w, nullptr, &tv) > 0; }
// before each c.write(): record sample; if(!socketWritable) { wait up to 8 s in 20 ms steps;
//   on expiry: dump ring + heap_caps_get_minimum_free_size() + SO_ERROR, then c.stop(); return false; }
```
```
SFU3 (T1):  C: "SFU3"+token  S: "OKAY"
            C: !H len, !Q size, 32B sha256, name      S: "RSME"+!Q offset  (0 = new)
            C: chunk(≤2 KiB) ...                       S: "ACKO"+!Q offset  (every 4 KiB, fsynced)
            C: (end)                                   S: "DONE"+sha256 (after hash+CSV validation+link publish)
```

## 5. Verification (see HARDWARE_TEST_PLAN HT-12 and revised HT-01)

| Prediction if memory-bound (H-b/c) | Prediction if not | Measure |
|---|---|---|
| `largest_free` in the 32-sample ring before the stalled write < ≈ 1.5 kB; `SO_ERROR`=0, socket unwritable | ring shows ≥ 3 kB free at the stall | T0 ring |
| window 2048/1024 or pre-write memory guard ⇒ **0 failures** over ≥ 3 000 windows; rate ≥ 5 kB/s with NoDelay | failures persist at the same rate per window | HT-12 matrix |
| PSRAM board (T4b) ⇒ 0 failures, ≥ 10 kB/s | no change | HT-13 (hardware swap) |
| VM `ss -ti`/journal: ESP32 goes silent (no retransmits) while server `bytes_received` flat | retransmits arrive (loss) | VM sampler |
| server journal pairs 1:1 with ESP32 `sent_bytes` | server reports fewer bytes than ESP32 accepted ⇒ loss | journal vs serial (needs the VM log of run 13) |

**To verify in the installed core (read-only, on the Arduino machine):** open `…/Arduino15/packages/esp32/hardware/esp32/3.3.11/tools/esp32-arduino-libs/esp32/sdkconfig` and report `CONFIG_LWIP_TCP_SND_BUF_DEFAULT`, `CONFIG_LWIP_TCP_WND_DEFAULT`, `CONFIG_LWIP_TCP_OVERSIZE_*`, `CONFIG_LWIP_TCP_RTO_TIME`, `CONFIG_ESP_WIFI_(DYNAMIC|STATIC)_TX_BUFFER*`, `CONFIG_MBEDTLS_ASYMMETRIC_CONTENT_LEN`, `CONFIG_MBEDTLS_SSL_PROTO_TLS1_3`, `CONFIG_SPIRAM*`. If `TCP_SND_BUF_DEFAULT` is ≤ 3 000 the send-buffer explanation (a) becomes viable and the plan stays valid (T2 helps either way).

## 6. Corrections to the first review
- F4 cause list: H-T1 (coexistence/BLE churn) and H-T2 (loss) are demoted for the *write failures*; H-T3 (heap) is promoted to **Likely**.
- F5 (Sheep-03): BLE ran 90 min flap-free at the same parameters, after reboots; the earlier 1 040 consecutive failures cannot be "offset luck" (each reconnect re-rolls offsets). A **backlog-burst trap** is a better fit (full ring ⇒ immediate back-to-back batches on every reconnect, never drains, self-sustaining; Sheep-03 alone and fresh restarts are fine; the two collars restored in logs 04–07 were also backlogged). *Hypothesis*; test = HT-04 cell e (induced backlog).
- Single best next test: HT-12 (was HT-01).
- Throughput: 5.5 kB/s mean (v1.3 run 1) is now 0.6–4.4 kB/s per attempt (run 2); the gap to 8.89 kB/s is larger than reported.
