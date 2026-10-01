#!/usr/bin/env python3
"""Acceptance-test helper for the hardware test plan: audit sealed hourly CSVs (or a folder of them)
for loss / duplication / reordering per (device_id, boot_id). Read-only; never modifies inputs.

  python csv_continuity_check.py <folder-or-files...> [--json out.json]

Per (device,boot) it reports: rows, batches, duplicate batches (same batch number seen twice - a
retry/dup that escaped suppression), batch-number gaps, sample_sequence gaps (= samples the collar
overwrote on ring overflow or skipped on missed deadlines), acquired_us non-monotonic steps,
max buffer_count (collar backlog depth), and an approximate acquisition-time reconstruction span.
Exit status 1 if any duplicate batch, partial batch or non-monotonic step is found.
"""
import argparse, collections, csv, glob, json, os, sys
ap = argparse.ArgumentParser(); ap.add_argument('paths', nargs='+'); ap.add_argument('--json'); a = ap.parse_args()
files = []
for p in a.paths: files += sorted(glob.glob(os.path.join(p, '*.csv'))) if os.path.isdir(p) else [p]
S = collections.defaultdict(lambda: {'rows': 0, 'batches': collections.Counter(), 'seq': [], 'us': [], 'maxbuf': 0, 'order': [], 'files': set()})
problems = []
for f in files:
    with open(f, newline='') as fh:
        r = csv.reader(fh); h = next(r, None)
        if not h or h[0] != 'gateway_received_gmt3': problems.append((f, 'header')); continue
        for n, row in enumerate(r, 2):
            if len(row) != 18: problems.append((f, f'line {n}: {len(row)} columns')); continue
            k = (int(row[2]), row[3]); s = S[k]; s['rows'] += 1; b = int(row[4]); s['batches'][b] += 1
            s['seq'].append(int(row[7])); s['us'].append(int(row[8])); s['maxbuf'] = max(s['maxbuf'], int(row[5])); s['files'].add(os.path.basename(f))
            if not s['order'] or s['order'][-1] != b: s['order'].append(b)
report = {}; bad = bool(problems)
for (dev, boot), s in sorted(S.items()):
    dup = sorted(b for b, c in s['batches'].items() if c > 25)
    partial = sorted(b for b, c in s['batches'].items() if c < 25)
    bs = sorted(s['batches']); gaps = [(x, y) for x, y in zip(bs, bs[1:]) if y != x + 1]
    seq = s['seq']; sgaps = [(x, y, y - x - 1) for x, y in zip(seq, seq[1:]) if y != x + 1 and y > x]
    back = sum(1 for x, y in zip(seq, seq[1:]) if y <= x); nonmono = sum(1 for x, y in zip(s['us'], s['us'][1:]) if y <= x)
    ooo = sum(1 for x, y in zip(s['order'], s['order'][1:]) if y < x)
    lost = sum(g[2] for g in sgaps)
    report[f'{dev}/{boot}'] = dict(rows=s['rows'], batches=len(bs), first_batch=bs[0], last_batch=bs[-1], duplicate_batches=dup, partial_batches=partial,
        batch_gaps=gaps[:10], batch_gap_count=len(gaps), sample_sequence_gaps=len(sgaps), samples_missing=lost, seconds_missing=round(lost / 25, 1),
        sequence_regressions=back, acquired_us_nonmonotonic=nonmono, out_of_order_batches=ooo, max_buffer_count=s['maxbuf'], files=len(s['files']))
    if dup or partial or back or nonmono or ooo: bad = True
for f, why in problems: print('PROBLEM', f, why)
for k, v in report.items():
    print(f'device/boot {k}: rows={v["rows"]} batches={v["first_batch"]}..{v["last_batch"]} ({v["batches"]}) gaps={v["batch_gap_count"]} missing_samples={v["samples_missing"]} (~{v["seconds_missing"]} s) '
          f'dup={v["duplicate_batches"]} partial={v["partial_batches"]} seq_regress={v["sequence_regressions"]} us_nonmono={v["acquired_us_nonmonotonic"]} ooo={v["out_of_order_batches"]} max_backlog={v["max_buffer_count"]} files={v["files"]}')
if a.json: json.dump(dict(problems=problems, streams=report), open(a.json, 'w'), indent=1)
print('RESULT:', 'FAIL' if bad else 'OK (no duplicates/partials/regressions; gaps above, if any, are collar-side loss)'); sys.exit(1 if bad else 0)
