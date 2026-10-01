#!/usr/bin/env python3
"""Crash-injection matrix over the REAL CsvStore.h (compiled on the host, see build.sh).

For every mutating syscall (open/write/fsync/rename/ftruncate) in a 12-batch, 3-hour scenario the
process is killed *before* that syscall (optionally with a torn half-write), files revert to their
last-fsynced content, then a fresh process recovers and the collar re-sends the last ACKed batch
plus everything unACKed. Invariant: every batch appears in sealed CSVs exactly once (25 rows).
Usage: python run_crash_matrix.py <bundle_root>
"""
import csv, collections, glob, os, shutil, subprocess, sys, tempfile
bundle = os.path.abspath(sys.argv[1]); here = os.path.dirname(os.path.abspath(__file__))
out = tempfile.mkdtemp(prefix='csvb')
subprocess.run([os.path.join(here, 'build.sh'), bundle, out], check=True, env=dict(os.environ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
H = os.path.join(out, 'harness'); SD = os.path.join(out, 'sd')

def run(*a):
    p = subprocess.run([H, *map(str, a)], cwd=out, capture_output=True, text=True); return p.returncode, p.stdout.strip()
def fresh():
    shutil.rmtree(SD, ignore_errors=True); os.makedirs(SD)
def acked():
    try: return len([l for l in open(os.path.join(SD, 'acked.txt')) if l.strip()])
    except FileNotFoundError: return 0
def rows():
    c = collections.Counter(); files = 0
    for f in sorted(glob.glob(os.path.join(SD, 'hourly', 'sheep_*.csv'))):
        files += 1
        r = csv.reader(open(f, newline='')); next(r)
        for row in r: c[(row[2], row[3], int(row[4]))] += 1
    return c, files
fresh(); rc, o = run('count'); total = int(o.split()[-1]); assert rc == 0
print(f'scenario mutating syscalls: {total}')
bad = []; stats = collections.Counter()
for torn in (0, 1):
    for n in range(1, total + 1):
        fresh(); rc, o = run('phase1', n, torn)
        if rc not in (77, 0): bad.append((n, torn, 'phase1', rc, o)); continue
        a = acked()
        rc2, o2 = run('phase2', a)
        if rc2 != 0: bad.append((n, torn, 'phase2', rc2, o2)); stats['recovery_halt'] += 1; continue
        c, files = rows()
        expect = {(str(1 + (i % 2)), '%016x' % (0x1111000000000000 + 1 + (i % 2)), i + 1) for i in range(12)}
        got = set(c)
        dup = [k for k, v in c.items() if v != 25]
        if got != expect or dup: bad.append((n, torn, 'integrity', 'missing=%s extra=%s badcount=%s' % (sorted(expect - got), sorted(got - expect), dup)))
        stats['ok' if got == expect and not dup else 'bad'] += 1
        stats['crashed' if rc == 77 else 'nocrash'] += 1
        left = glob.glob(os.path.join(SD, 'hourly', '*.open'))
        stats['runs_with_unreferenced_open_leftover'] += bool(left)
        stats['leftover_open_with_data_rows'] += sum(1 for f in left if open(f,'rb').read().count(b'\n') > 1)
print(dict(stats))
if bad:
    print('FAILURES:'); [print(' ', b) for b in bad[:20]]; sys.exit(1)
print('PASS: no loss, no duplicate rows, recovery always succeeded across all injected crash points (process-crash/last-fsync model)')
shutil.rmtree(out, ignore_errors=True)
