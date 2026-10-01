#!/usr/bin/env python3
"""Re-derive every number quoted from the evidence logs. Usage: python evidence_analysis.py <bundle_root>"""
import collections, datetime as dt, re, statistics, sys
from pathlib import Path
ev = Path(sys.argv[1]) / 'evidence'
# --- 09: server-side view of the failed SFU2 uploads
recv = {}; rows = []
for l in open(ev / '09_vm_endurance_failure.txt', errors='ignore'):
    m = re.search(r'(\d\d:\d\d:\d\d),\d+ (INFO Receiving|WARNING Upload failed).*?(?:received_bytes=(\d+) error=(\w+))?', l)
    t = re.search(r' (\d\d:\d\d:\d\d),', l)
    if not t: continue
    ts = dt.datetime.strptime(t.group(1), '%H:%M:%S')
    if 'INFO Receiving' in l: recv = ts
    elif 'Upload failed' in l:
        b = int(re.search(r'received_bytes=(\d+)', l).group(1)); e = re.search(r'error=(\w+)', l).group(1)
        rows.append((b, e, (ts - recv).seconds))
print(f'[09] failed SFU2 attempts: {len(rows)}   completed: 0')
c = collections.Counter(b for b, _, _ in rows); print('     bytes received before failure:', dict(sorted(c.items())))
print('     bytes mod 512 == 0 for all:', all(b % 512 == 0 for b, _, _ in rows), '(client writes 512-B TLS records)')
d = [s for b, e, s in rows if e == 'IncompleteReadError']; t0 = [s for b, e, s in rows if e == 'TimeoutError']
print(f'     IncompleteReadError (peer closed): n={len(d)} duration after metadata median {statistics.median(d)} s (min {min(d)}, max {max(d)})  <- ~15 s socket no-progress timeout + a few s of trickle')
print(f'     TimeoutError (server 30 s read, 0 B): n={len(t0)} durations {sorted(set(t0))}')
print('     partial in 2nd window (received_bytes>4096):', [b for b, _, _ in rows if b > 4096])
# --- 08: ESP32 view
L = open(ev / '08_esp32_endurance_failure.txt', errors='ignore').read().splitlines()
ctr = lambda p: sum(1 for l in L if re.search(p, l))
print(f'\n[08] UPLOAD starting={ctr("^UPLOAD starting")} failed={ctr("^UPLOAD failed")} verified={ctr("UPLOAD verified")}  "Closing connection on failed write" (core log_e)={ctr("Closing connection on failed write")}')
print(f'     mbedTLS handle_error() log lines (would appear for alloc/protocol errors at Warn level)={ctr("_handle_error")} (only the handshake-EOF when server was busy)')
print(f'     Sheep-03 connecting={ctr("Sheep-03: connecting")}  disconnected reason=520={ctr("reason=520")}  connect failed={ctr("Sheep-03: connect failed")}   Sheep-03 batch lines={ctr("^Sheep-03 batch")}')
ev3 = [(i, 'D' if 'disconnected' in l else 'C' if 'connected;' in l else 'T') for i, l in enumerate(L) if l.startswith('Sheep-03') and ('disconnected' in l or 'connected;' in l or 'connecting' in l)]
gap = lambda a, b: sum(1 for l in L[a:b] if l.startswith('Sheep-01 batch'))   # Sheep-01 delivers ~1 batch/s => seconds proxy
up = [gap(i, j) for (i, t), (j, u) in zip(ev3, ev3[1:]) if t == 'C' and u == 'D']; cn = [gap(i, j) for (i, t), (j, u) in zip(ev3, ev3[1:]) if t == 'T' and u == 'C']
print(f'     link-up lifetime (s, proxy) median {statistics.median(up)} range {min(up)}-{max(up)}; connect time median {statistics.median(cn)} range {min(cn)}-{max(cn)}; -> cycle ~{statistics.median(up)+statistics.median(cn)+2:.0f} s for hours')
sd = [int(re.search(r'max_save_ms=(\d+)', l).group(1)) for l in L if l.startswith('SD=') and 'max_save_ms=' in l]; print(f'     max_save_ms: first {sd[0]}, last {sd[-1]} (grows over hours: card/FAT behaviour, not a per-batch average)')
hp = [(int(re.search(r'free_heap=(\d+)', l).group(1)), int(re.search(r'largest_free=(\d+)', l).group(1)), 'ACTIVE' in l) for l in L if l.startswith('SD=') and 'free_heap=' in l and 'largest_free=' in l]
act = [h for h in hp if h[2]]; idl = [h for h in hp if not h[2]]
print(f'     heap while upload ACTIVE: free {min(h[0] for h in act)}..{max(h[0] for h in act)}, largest {min(h[1] for h in act)}..{max(h[1] for h in act)} ; IDLE: free ~{statistics.median(h[0] for h in idl):.0f}, largest {statistics.median(h[1] for h in idl):.0f}')
# --- 01: the single success happened while Sheep-03 was flapping
M = open(ev / '01_cloud_first_success_and_ble_failures.txt', errors='ignore').read().splitlines()
s = max(i for i, l in enumerate(M[:2335]) if l.startswith('UPLOAD starting')); e = next(i for i, l in enumerate(M) if 'UPLOAD verified' in l)
w = M[s:e]; print(f'\n[01] the ONE verified upload (968,578 B): lines {s+1}-{e+1}; Sheep-03 reason=520 disconnects during it: {sum("reason=520" in l for l in w)}, connect attempts: {sum("Sheep-03: connecting" in l for l in w)}  -> BLE flapping did not by itself prevent a 125 s TLS transfer')
print(f'     failed attempts before it in same log: {sum(l.startswith("UPLOAD failed") for l in M[:s])}')
