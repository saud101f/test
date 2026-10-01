#!/usr/bin/env python3
"""Self-test: inject a duplicate batch, a gap (overflow) and a partial batch into synthetic CSVs and check the tool reports each."""
import os, subprocess, sys, tempfile
HDR = 'gateway_received_gmt3,gateway_uptime_ms,device_id,boot_id,batch_number,buffer_count,sample_index,sample_sequence,acquired_us,x_mg,y_mg,z_mg,gyro_x_raw,gyro_y_raw,gyro_z_raw,gx_dps,gy_dps,gz_dps\n'
def batch(dev, b, seq0, rows=25):
    return ''.join(f'2026-10-01T10:00:00+03:00,1,{dev},00000000000000a{dev},{b},25,{i},{seq0+i},{(seq0+i)*40000},1,2,3,0,0,0,0.00,0.00,0.00\n' for i in range(rows))
d = tempfile.mkdtemp(); here = os.path.dirname(os.path.abspath(__file__)); tool = os.path.join(here, 'csv_continuity_check.py')
clean = HDR + ''.join(batch(1, b, b * 25) for b in range(1, 6)); open(f'{d}/a.csv', 'w').write(clean)
r = subprocess.run([sys.executable, tool, d], capture_output=True, text=True); assert r.returncode == 0, r.stdout; print('clean ->', r.stdout.strip().splitlines()[-1][:40])
open(f'{d}/b.csv', 'w').write(HDR + batch(2, 1, 25) + batch(2, 3, 75))                       # gap: batch 2 missing -> 25 samples lost (not a failure)
r = subprocess.run([sys.executable, tool, d], capture_output=True, text=True); assert r.returncode == 0 and 'missing_samples=25' in r.stdout, r.stdout; print('gap detected')
open(f'{d}/c.csv', 'w').write(HDR + batch(3, 1, 25) + batch(3, 1, 25))                       # duplicate batch escaped suppression
r = subprocess.run([sys.executable, tool, d], capture_output=True, text=True); assert r.returncode == 1 and 'dup=[1]' in r.stdout, r.stdout; print('duplicate detected')
os.remove(f'{d}/c.csv'); open(f'{d}/e.csv', 'w').write(HDR + batch(3, 1, 25, rows=17))        # partial batch
r = subprocess.run([sys.executable, tool, d], capture_output=True, text=True); assert r.returncode == 1 and 'partial=[1]' in r.stdout, r.stdout; print('partial detected')
print('PASS')
