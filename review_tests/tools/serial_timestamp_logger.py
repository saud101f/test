#!/usr/bin/env python3
"""Timestamp every line of the ESP32/MG24 serial output with the host UTC clock so it can be aligned with
VM journal/`ss` samples. Optionally sends commands (e.g. 'uploads on') at given offsets. Read-only otherwise.

  python serial_timestamp_logger.py --port COM7 --out run01.log [--send 60:"uploads on" --send 3600:"uploads off"]
(115200 baud, Newline termination as required by Diagnostics.h). Do not use while the Arduino Serial Monitor holds the port.
"""
import argparse, datetime, sys, threading, time
try: import serial
except ImportError: sys.exit('pip install pyserial')
ap = argparse.ArgumentParser(); ap.add_argument('--port', required=True); ap.add_argument('--baud', type=int, default=115200)
ap.add_argument('--out', required=True); ap.add_argument('--send', action='append', default=[], help='SECONDS:command'); ap.add_argument('--duration', type=float, default=0)
a = ap.parse_args(); ser = serial.Serial(a.port, a.baud, timeout=0.2); t0 = time.time(); stop = False
def sender():
    for item in sorted(a.send, key=lambda s: float(s.split(':', 1)[0])):
        at, cmd = item.split(':', 1)
        while time.time() - t0 < float(at):
            if stop: return
            time.sleep(0.2)
        ser.write((cmd + '\n').encode()); print(f'[sent @{time.time()-t0:.0f}s] {cmd}', file=sys.stderr)
threading.Thread(target=sender, daemon=True).start()
buf = b''
with open(a.out, 'a', buffering=1) as out:
    out.write(f'# start {datetime.datetime.now(datetime.timezone.utc).isoformat()} port={a.port}\n')
    try:
        while not (a.duration and time.time() - t0 > a.duration):
            buf += ser.read(512)
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                out.write(datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S.%f')[:-3] + 'Z ' + line.decode('utf-8', 'replace').rstrip('\r') + '\n')
    except KeyboardInterrupt: pass
stop = True
