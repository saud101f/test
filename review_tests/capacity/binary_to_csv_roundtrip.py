#!/usr/bin/env python3
"""Evidence for the 'binary archive + server-side CSV materialisation' option: the CSV is a pure function of
(617-byte BatchPacket, gateway receive time, gateway uptime). Check byte-exact equality against the rows
emitted by the REAL CsvStore.h (host build, see ../csvstore_crash/build.sh) and exhaustively compare the
'%.2f' gyro conversion of every int16 against C printf. Host libc only: ESP32 newlib float printf is NOT tested.
Usage: python binary_to_csv_roundtrip.py <bundle_root>
"""
import glob, os, struct, subprocess, sys, tempfile, shutil, ctypes, ctypes.util
bundle = os.path.abspath(sys.argv[1]); here = os.path.dirname(os.path.abspath(__file__))
out = tempfile.mkdtemp(prefix='rt'); sd = os.path.join(out, 'sd'); os.makedirs(sd)
subprocess.run([os.path.join(here, '../csvstore_crash/build.sh'), bundle, out], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
assert subprocess.run([os.path.join(out, 'harness'), 'count'], cwd=out, capture_output=True).returncode == 0
real = ''.join(open(f, newline='').read().split('\n', 1)[1] for f in sorted(glob.glob(os.path.join(sd, 'hourly', 'sheep_*'))))
# re-create the harness's deterministic batches as PACKED BINARY (same layout as the wire/SD format) and render from bytes only
def pack(dev, batch):
    hdr = struct.pack('<BBQIBH', 3, dev, 0x1111000000000000 + dev, batch, 25, 25); body = b''
    for i in range(25):
        body += struct.pack('<hhhhhhQI', dev*100+i, -batch, 1000, i, -i, batch, 1000000*batch + 40000*i, batch*25 + i)
    return hdr + body
assert len(pack(1, 1)) == 617
import time
def render(pkt, rx_time, uptime_ms):
    ver, dev, boot, batch, cnt, buf = struct.unpack_from('<BBQIBH', pkt, 0); lines = []
    ts = time.strftime('%Y-%m-%dT%H:%M:%S+03:00', time.gmtime(rx_time + 10800))
    for i in range(25):
        ax, ay, az, gx, gy, gz, us, seq = struct.unpack_from('<hhhhhhQI', pkt, 17 + 24 * i)
        lines.append('%s,%d,%d,%016x,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%.2f,%.2f,%.2f\n' % (ts, uptime_ms, dev, boot, batch, buf, i, seq, us, ax, ay, az, gx, gy, gz, gx*.07, gy*.07, gz*.07))
    return ''.join(lines)
T0 = 1790000000; mine = ''
for i in range(12): mine += render(pack(1 + i % 2, i + 1), T0 + (i // 4) * 3600 + i, i * 1000)
# the real files are concatenated in name order == time order for this scenario
print('byte-exact CSV regeneration from packed binary vs real CsvStore output:', mine == real, f'({len(real)} bytes)')
# exhaustive int16 -> "%.2f" of raw*.07 versus C printf (host libc)
libc = ctypes.CDLL(ctypes.util.find_library('c')); buf = ctypes.create_string_buffer(64); bad = 0
for raw in range(-32768, 32768):
    libc.snprintf(buf, 64, b'%.2f', ctypes.c_double(raw * .07))
    if buf.value.decode() != '%.2f' % (raw * .07): bad += 1
print('all 65536 gyro raw values: Python %.2f == host-C printf %.2f ->', 'PASS' if bad == 0 else f'FAIL ({bad})')
shutil.rmtree(out, ignore_errors=True); sys.exit(0 if mine == real and bad == 0 else 1)
