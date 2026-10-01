#!/bin/sh
# usage: build.sh <bundle_root> <outdir>
set -e
B="$1"; O="$2"; mkdir -p "$O/inc"
SRC="$B/outputs/hourly_sd_cloud/ESP32_Hourly_SD_Cloud"
# esp_timer stub
echo '#pragma once' > "$O/inc/esp_timer.h"
# extract the real packet structs (AccelSample..BatchPacket) verbatim
{ echo '#pragma once'; echo '#include <stdint.h>'; sed -n '/^struct __attribute__((packed)) AccelSample/,/^};/p;/^struct __attribute__((packed)) TimedSample/,/^};/p;/^struct __attribute__((packed)) BatchPacket/,/^};/p' "$SRC/ESP32_Hourly_SD_Cloud.ino" | tr -d '\r'; echo 'static_assert(sizeof(BatchPacket)==617,"layout");'; } > "$O/inc/device_types.h"
# rewrite only the hard-coded mount path for host use; logic untouched
# relative root "sd" (run with cwd=$O) keeps the original fixed-size path buffers valid
sed "s#/sd/hourly#sd/hourly#g" "$SRC/CsvStore.h" > "$O/inc/CsvStore_host.h"
if [ "$FIRST_BOOT_FIX" = "1" ]; then
  # PROPOSED patch (host copy only): ignore our own checkpoint*.bin when deciding whether the card holds data
  python3 - "$O/inc/CsvStore_host.h" <<'PY'
import sys
p=sys.argv[1]; s=open(p).read()
old='if(strcmp(e->d_name,".") && strcmp(e->d_name,"..")) existing=true;'
new='if(strcmp(e->d_name,".") && strcmp(e->d_name,"..") && strncmp(e->d_name,"checkpoint",10)) existing=true;'
assert old in s; open(p,'w').write(s.replace(old,new))
PY
fi
g++ -std=c++17 -O1 -Wall -Wno-unused-function -I"$O/inc" -I"$(dirname "$0")" -DSDROOT="\"sd\"" -Wno-format-truncation -Wno-unused-result -o "$O/harness" "$(dirname "$0")/harness.cpp"
