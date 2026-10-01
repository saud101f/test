#!/bin/sh
# usage: ./run_all.sh <bundle_root>      (run from the review_tests directory or anywhere)
set -e
B="$(cd "$1" && pwd)"; H="$(cd "$(dirname "$0")" && pwd)"
echo "== bundle's own tests";                 (cd "$B" && python3 work/test_hourly_server.py 2>&1 | tail -4)
echo "== crash matrix (as shipped; expect 4 first-boot failures)"; python3 "$H/csvstore_crash/run_crash_matrix.py" "$B" 2>&1 | tail -7 || true
echo "== crash matrix with proposed first-boot fix"; FIRST_BOOT_FIX=1 python3 "$H/csvstore_crash/run_crash_matrix.py" "$B" 2>&1 | tail -3
echo "== BLE fragment assembler fuzz";        python3 "$H/ble_assembly/run_fuzz.py" "$B"
echo "== harness self-test (mutant must be caught)"; python3 "$H/ble_assembly/run_fuzz.py" "$B" mutate | grep trials= || true
echo "== server pre-auth slot repro";         python3 "$H/server/repro_busy_slot_dos.py" "$B" 2>&1 | grep -v WARNING
echo "== capacity model";                     python3 "$H/capacity/capacity_model.py" "$B"
echo "== binary->CSV round trip";             python3 "$H/capacity/binary_to_csv_roundtrip.py" "$B"
echo "== evidence analysis";                  python3 "$H/evidence/evidence_analysis.py" "$B"
echo "== tool self-tests";                    python3 "$H/tools/test_csv_continuity_check.py" | tail -1; python3 "$H/tools/test_serial_timestamp_logger.py"
