#!/usr/bin/env python3
"""Self-test over a pseudo-terminal: lines get timestamps, and a scheduled command is delivered with a newline."""
import os, subprocess, sys, tempfile, time, pty
here = os.path.dirname(os.path.abspath(__file__)); master, slave = pty.openpty(); name = os.ttyname(slave); out = tempfile.mktemp()
p = subprocess.Popen([sys.executable, os.path.join(here, 'serial_timestamp_logger.py'), '--port', name, '--out', out, '--send', '1:uploads on', '--duration', '3'], stderr=subprocess.PIPE)
time.sleep(0.5); os.write(master, b'SD=READY saved_batches=1\r\nDIAG uptime_ms=5\r\n'); time.sleep(1.5)
os.set_blocking(master, False)
try: got = os.read(master, 100)
except BlockingIOError: got = b''
p.wait(); text = open(out).read()
assert got == b'uploads on\n' or got == b'uploads on\r\n', got
assert 'SD=READY saved_batches=1' in text and text.count('Z ') == 2, text
print('PASS', text.splitlines()[1][:40])
