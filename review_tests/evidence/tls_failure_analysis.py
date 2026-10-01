#!/usr/bin/env python3
"""Re-derive the TLS-failure invariants from evidence/13 (and 09 if present).
Usage: python tls_failure_analysis.py <bundle_root_with_evidence_13>"""
import collections, re, statistics as st, sys
from pathlib import Path
ev = Path(sys.argv[1]) / 'evidence'
L = open(ev / '13_v13_upload_failures_ble_stable.txt', errors='ignore').read().splitlines()
up = 0; prog = []; fails = []; heap = []; rssi = []; disc = set(); inval = set(); errno = collections.Counter(); stages = collections.Counter()
rates = {}; seg = 0
for l in L:
    m = re.search(r'^(?:DIAG|BLE DIAG).*uptime_ms=(\d+)', l)
    if m: up = int(m.group(1))
    m = re.search(r'disconnects=(\d+) invalid=(\d+)', l)
    if m: disc.add(int(m.group(1))); inval.add(int(m.group(2)))
    m = re.match(r'DIAG .*rssi=(-?\d+)', l)
    if m: rssi.append(int(m.group(1)))
    m = re.match(r'SD=.*upload=(\w+).*free_heap=(\d+) largest_free=(\d+)', l)
    if m: heap.append((m.group(1), int(m.group(2)), int(m.group(3))))
    if l.startswith('UPLOAD starting'): seg += 1; prog = []
    elif l.startswith('UPLOAD progress'):
        prog.append((up, int(re.search(r'(\d+)/', l).group(1)))); rates.setdefault(seg, []).append(prog[-1])
    elif 'op=write-failed' in l:
        d = dict(re.findall(r'(\w+)=(-?\d+)', l)); stages[re.search(r'stage=(\w+)', l).group(1)] += 1; errno[d['errno']] += 1
        fails.append((int(d['sent_bytes']), int(d['uptime_ms']), (int(d['uptime_ms']) - prog[-1][0]) / 1000 if prog and prog[-1][0] else None,
                      prog[-1][1] if prog else None, int(d['rssi'])))
print(f'[13] write-failed events in this excerpt: {len(fails)}  stages={dict(stages)}  errno={dict(errno)}')
print('     sent_bytes at failure:', sorted(f[0] for f in fails))
print('     sent_bytes mod 512 :', sorted(set(f[0] % 512 for f in fails)), '   mod 4096:', sorted(set(f[0] % 4096 for f in fails)),
      ' -> every failure is 5,6 or 7 records into a 4096-B window' if set(f[0] % 4096 for f in fails) <= {2560, 3072, 3584} else '')
gap = [f[2] for f in fails if f[2] is not None]
print(f'     seconds from last "UPLOAD progress" print to failure: {sorted(round(g,1) for g in gap)}  (print period >=10 s + 15 s no-progress timeout bounds this to 15-25 s)')
print('     rssi at failure:', sorted(f[4] for f in fails))
for s, p in rates.items():
    if len(p) < 8: continue
    r = [(b[1] - a[1]) / 1000 / ((b[0] - a[0]) / 1000) for a, b in zip(p, p[1:]) if 0 < b[0] - a[0] < 30000]
    print(f'     attempt#{s}: {p[0][1]}..{p[-1][1]} B  windows={len(r)} rate kB/s median {st.median(r):.2f} mean {st.mean(r):.2f} p10 {sorted(r)[len(r)//10]:.2f} p90 {sorted(r)[9*len(r)//10]:.2f}')
act = [h for h in heap if h[0] == 'ACTIVE']
print(f'     heap sampled every 5 s while ACTIVE (n={len(act)}): free min {min(h[1] for h in act)} median {st.median(h[1] for h in act):.0f}; largest block min {min(h[2] for h in act)} median {st.median(h[2] for h in act):.0f}')
print(f'     NOTE: heap printed AFTER a failure (~45-49 kB) is post-stop() and says nothing about the failure instant')
print(f'     BLE counters over the whole excerpt: disconnects seen {sorted(disc)}, invalid {sorted(inval)}; DIAG rssi range {min(rssi)}..{max(rssi)}')
# TLS record arithmetic
rec = 512 + 29
print(f'\n[arith] TLS1.2-GCM record = 512+29 = {rec} B on the wire; 5/6/7 records = {5*rec}/{6*rec}/{7*rec} B; lwIP MSS=1436 -> {5*rec/1436:.1f}/{6*rec/1436:.1f}/{7*rec/1436:.1f} segments')
print('       ~2 full segments (2872 B) are accepted, the 3rd allocation fails when the heap cannot supply it (MEM_LIBC_MALLOC=1, MEMP_MEM_MALLOC=1 in IDF lwipopts.h)')
