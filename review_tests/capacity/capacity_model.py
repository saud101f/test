#!/usr/bin/env python3
"""Quantitative model for capture / storage / upload. Every input is either (a) read from the bundle's
evidence/sources, (b) a stated assumption, or (c) clearly labelled synthetic. Nothing here is a hardware measurement.
Usage: python capacity_model.py <bundle_root>
"""
import bz2, glob, lzma, os, random, re, struct, sys, tempfile, time, zlib
from pathlib import Path
bundle = Path(sys.argv[1]).resolve(); sys.path.insert(0, str(bundle / 'outputs/hourly_sd_cloud'))
import sheep_file_server as srv
ev = bundle / 'evidence'

# ---------- (a) evidence-derived ----------
def progress_rates(fname):
    last = None; pts = []
    for l in open(ev / fname, errors='ignore'):
        m = re.match(r'DIAG uptime_ms=(\d+)', l)
        if m: last = int(m.group(1))
        m = re.match(r'UPLOAD progress: (\d+)/(\d+)', l)
        if m and last is not None: pts.append((last, int(m.group(1))))
    return pts
pts = progress_rates('11_v13_upload_progress.txt')
span = (pts[-1][0] - pts[0][0]) / 1000; kbs = (pts[-1][1] - pts[0][1]) / 1000 / span
first = (pts[1][1] - pts[0][1]) / 1000 / ((pts[1][0] - pts[0][0]) / 1000); last = (pts[-1][1] - pts[-2][1]) / 1000 / ((pts[-1][0] - pts[-2][0]) / 1000)
print(f'[evidence 11] v1.3 upload: {pts[0][1]}->{pts[-1][1]} B in {span:.0f}s = {kbs:.1f} kB/s mean; first 10s window {first:.1f} kB/s, last {last:.1f} kB/s')
hash_s = 395.851 - 360.698
print(f'[evidence 10/11] pre-TLS SHA-256 pass: 9,852,039 B in {hash_s:.1f}s = {9852039/hash_s/1000:.0f} kB/s (SD read + hash)')
rows_obs = 968578 / 8175; print(f'[evidence 01/README] first verified file: 968,578 B / 8,175 rows = {rows_obs:.1f} B/row')

# ---------- (b) assumptions ----------
HZ, COLLARS = 25, 3; G_rows = HZ * COLLARS; ROW = rows_obs
G = G_rows * ROW; hour = G * 3600; day = hour * 24
print(f'\n[capture] {G_rows} rows/s x {ROW:.1f} B = {G/1000:.2f} kB/s ; {hour/1e6:.1f} MB/hour ; {day/1e6:.0f} MB/day ({G_rows*3600} rows/h)')
sd = 30424 * 1048576; cloud = 30e9 - 1024**3 - 4e9   # 30GB disk, 1 GiB server reserve, ~4 GB OS/software assumption
print(f'[storage] SD 30424 MiB: full in {sd/day:.1f} days of CSV (no deletion; first failure = "SD FAULT", ACKs stop)')
print(f'[storage] VM ~30 GB disk, 1 GiB reserve, ~4 GB OS assumed: reject new uploads after ~{cloud/day:.0f} days')
bin_batch = 617 + 16; bin_hour = 3 * 3600 * bin_batch; print(f'[alt] binary batch log ({bin_batch} B/batch incl. 16 B gateway meta): {bin_hour/1e6:.1f} MB/hour = {hour/bin_hour:.1f}x smaller; SD full in {sd/(bin_hour*24):.0f} days')

print('\n[upload ceiling] 4096-B stop-and-wait window (SFU2) vs RTT (RTT is NOT measured; intercontinental path typically ~0.2 s, verify with a ping/TCP-RTT capture)')
print('  RTT    window=4K   8K    16K   32K   | sndbuf(5760 B, IDF lwip default)/RTT cap')
for rtt in (0.10, 0.15, 0.22, 0.30):
    print(f'  {rtt*1000:3.0f}ms  ' + ' '.join(f'{w/(rtt+0.04)/1000:6.1f}' for w in (4096, 8192, 16384, 32768)) + f'   | {5760/rtt/1000:5.1f} kB/s   (window/(RTT+40ms delayed-ACK) ; kB/s)')
print(f'  generation = {G/1000:.2f} kB/s. Even the idealised 4 KiB window at 0.22 s RTT is {4096/(0.26)/1000:.1f} kB/s -> margin x{4096/0.26/G:.2f}; measured mean {kbs:.1f} kB/s = x{kbs*1000/G:.2f}')
print('\n[backlog drain] files of one hour each; time to clear N backlog files while new data keeps arriving (drain rate = U - G)')
for U in (kbs, 8.6, 12, 20, 40):
    d = U * 1000 - G
    print(f'  U={U:5.1f} kB/s  net={d/1000:+6.2f} kB/s  ' + ('NEVER (backlog grows %.1f MB/h)' % (-d*3600/1e6) if d <= 0 else 'clear 15 files in %.1f h' % (15 * hour / d / 3600)))
print(f'  hash pre-pass for one full-hour file at 281 kB/s: {hour/281e3:.0f} s per ATTEMPT (v1.3 repeats it every retry)')

# ---------- (c) synthetic data: compression + server validation time ----------
def synth(profile, n_batches):
    rnd = random.Random(1); sd_a, sd_g = {'quiet': (6, 40), 'active': (180, 2500)}[profile]
    rows = []; seq = 1; us = 10_000_000_000; ax = ay = 0; az = 1000
    for b in range(n_batches):
        dev = 1 + b % 3; ts = '2026-10-01T10:%02d:%02d+03:00' % ((b // 180) % 60, (b // 3) % 60); up = 1_000_000 + b * 333
        for i in range(25):
            ax += rnd.gauss(0, sd_a / 8); ay += rnd.gauss(0, sd_a / 8); az += rnd.gauss(0, sd_a / 8)
            ax *= .98; ay *= .98; az = 1000 + (az - 1000) * .98
            g = [max(-32768, min(32767, int(rnd.gauss(0, sd_g)))) for _ in range(3)]
            rows.append('%s,%d,%d,%016x,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%.2f,%.2f,%.2f\n' % (ts, up, dev, 0x1111000000000000 + dev, b // 3 + 1, 25, i, seq, us, int(ax), int(ay), int(az), *g, *(x * .07 for x in g)))
            seq += 1; us += 40000
    return ''.join(rows)
hdr = srv.HEADER + '\n'
print('\n[SYNTHETIC data - indicative only; real sensor entropy unknown]  one hour = 3600 batches/collar x 3')
for prof in ('quiet', 'active'):
    # one hour = 270000 rows = 10800 batches
    body = synth(prof, 10800); raw = (hdr + body).encode(); n = len(raw)
    z6 = len(zlib.compress(raw, 6)); x = len(lzma.compress(raw, preset=6)); bz = len(bz2.compress(raw, 9))
    print(f'  {prof:6s}: CSV {n/1e6:5.1f} MB ({n/270000:.1f} B/row) | zlib-6 {n/z6:4.1f}x ({z6/1e6:4.1f} MB) | xz {n/x:4.1f}x | bz2 {n/bz:4.1f}x')
    if prof == 'active':
        p = Path(tempfile.mkdtemp()) / 'sheep_t.csv'; p.write_bytes(raw)
        t = time.time(); rows = srv.validate_csv(p); tv = time.time() - t; t = time.time(); srv.digest_file(p); td = time.time() - t
        print(f'  server validate_csv({rows} rows) on THIS host: {tv:.1f}s ; sha256 {td:.2f}s . ESP32 waits 30 s for the final DONE (HourlyRuntime.h:78 pattern). '
              f'An e2-micro (shared 0.25-1 vCPU, not measured) at 2x-4x slower => {tv*2:.0f}-{tv*4:.0f}s')
