#!/usr/bin/env python3
"""Fuzz the REAL Collar::handleNotify() fragment assembler (extracted verbatim from the .ino at build
time, FreeRTOS/NimBLE types stubbed) against a reference model of the documented protocol.
Usage: python run_fuzz.py <bundle_root> [mutate]
"""
import os, re, subprocess, sys, tempfile, random, json
bundle = os.path.abspath(sys.argv[1]); out = tempfile.mkdtemp(prefix='blefz')
ino = open(os.path.join(bundle, 'outputs/hourly_sd_cloud/ESP32_Hourly_SD_Cloud/ESP32_Hourly_SD_Cloud.ino'), newline='').read().replace('\r\n', '\n')
def grab(start, end):
    a = ino.index(start); b = ino.index(end, a); return ino[a:b]
consts = grab('constexpr uint8_t BATCH_SAMPLES', 'static NimBLEUUID serviceUuid')  # BATCH_SAMPLES.. COLLAR_COUNT
structs = grab('struct __attribute__((packed)) AccelSample', '#include "HourlyRuntime.h"')
members = grab('  portMUX_TYPE mailboxMux', '  void onConnect') if False else grab('  portMUX_TYPE mailboxMux', '  BatchPacket assembled = {};')
members2 = grab('  BatchPacket assembled = {};', '    void onConnect(')
handler = grab('  void handleNotify(', '  bool connectCollar()')
if len(sys.argv) > 2 and sys.argv[2] == 'mutate':   # self-test: the harness must catch a deliberately broken assembler
    assert 'header.batchNumber == assemblingBatch' in handler
    handler = handler.replace('header.batchNumber == assemblingBatch', 'true')
src = f'''
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <cstddef>
#include <vector>
#include <atomic>
#include <cstdlib>
typedef int portMUX_TYPE; 
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(x) ((void)0)
#define portEXIT_CRITICAL(x) ((void)0)
static uint32_t millis() {{ return 0; }}
struct NimBLERemoteCharacteristic {{}};
{consts.split('static const char COLLAR_1_ADDRESS')[0]}
{structs}
struct Collar {{
  const uint8_t DEVICE_ID = 1;
  uint32_t _pad;
{members}
{members2}
{handler}
}};
// ---- reference model: a batch is accepted iff fragments 0,1,2 of the SAME batch arrive consecutively,
// ---- with exact lengths, valid header, valid payload, and nothing else in between.
struct Frag {{ uint16_t magic; uint32_t batch; uint8_t idx, cnt; std::vector<uint8_t> payload; bool notify=true; }};
static BatchPacket mk(uint32_t batch, uint8_t dev=1, bool badSeq=false) {{
  BatchPacket p; memset(&p,0,sizeof p); p.version=3; p.deviceID=dev; p.bootID=0xabc; p.batchNumber=batch; p.sampleCount=25; p.bufferCount=25;
  for(int i=0;i<25;++i){{ p.samples[i].sequence=batch*25+i; p.samples[i].acquiredUs=1000+40000ULL*i+batch*1000000ULL; }}
  if(badSeq) p.samples[5].acquiredUs=p.samples[4].acquiredUs; return p; }}
static std::vector<Frag> frags(const BatchPacket&p){{ std::vector<Frag> v; const uint8_t*b=(const uint8_t*)&p;
  for(int i=0;i<3;++i){{ size_t off=i*FRAGMENT_BYTES; size_t n=sizeof p-off<FRAGMENT_BYTES?sizeof p-off:FRAGMENT_BYTES; Frag f{{FRAME_MAGIC,p.batchNumber,(uint8_t)i,3,std::vector<uint8_t>(b+off,b+off+n)}}; v.push_back(f);}} return v; }}
static bool refValid(const BatchPacket&p){{ if(p.version!=3||p.deviceID!=1||p.sampleCount!=25||p.bufferCount>1365) return false;
  for(int i=1;i<25;++i){{ uint32_t st=p.samples[i].sequence-p.samples[i-1].sequence; if(!(p.samples[i].acquiredUs>p.samples[i-1].acquiredUs&&st>0&&st<0x80000000UL)) return false; }} return true; }}
int main(int argc,char**argv){{
  unsigned seed=argc>1?atoi(argv[1]):1; srand(seed); long wrongAccept=0, missed=0, trials=0, accepted=0;
  Collar c; c.linkUp=true; c.connectionEpoch=1; 
  for(int t=0;t<200000;++t){{
    // build a random stream of fragments from several batches, with drops/dups/reorders/corruptions
    std::vector<Frag> stream; std::vector<BatchPacket> batches;
    int nb=1+rand()%3; for(int b=0;b<nb;++b){{ bool bad=(rand()%10==0); BatchPacket p=mk(100+rand()%5+b*10,1,bad); if(rand()%12==0) p.deviceID=2; if(rand()%15==0) p.version=2; batches.push_back(p); auto f=frags(p); for(auto&x:f) stream.push_back(x); }}
    int ops=rand()%4; for(int o=0;o<ops;++o){{ if(stream.empty()) break; int i=rand()%stream.size(); switch(rand()%6){{
      case 0: stream.erase(stream.begin()+i); break;                       // drop fragment
      case 1: stream.insert(stream.begin()+i,stream[i]); break;           // duplicate
      case 2: if(i+1<(int)stream.size()) std::swap(stream[i],stream[i+1]); break; // reorder
      case 3: stream[i].payload.pop_back(); break;                         // truncate (length error)
      case 4: stream[i].cnt=2; break;                                       // bad count
      case 5: stream[i].batch^=1; break; }} }}                              // header batch mismatch
    // reference: scan for consecutive 0,1,2 of same batch with valid lengths
    std::vector<uint32_t> expect; for(size_t i=0;i+2<stream.size();++i){{ auto&a=stream[i],&b=stream[i+1],&d=stream[i+2];
      auto ok=[&](Frag&f,int idx){{ size_t off=idx*FRAGMENT_BYTES; size_t n=sizeof(BatchPacket)-off<FRAGMENT_BYTES?sizeof(BatchPacket)-off:FRAGMENT_BYTES; return f.magic==FRAME_MAGIC&&f.cnt==3&&f.idx==idx&&f.payload.size()==n; }};
      if(ok(a,0)&&ok(b,1)&&ok(d,2)&&a.batch==b.batch&&b.batch==d.batch){{ BatchPacket p; uint8_t*q=(uint8_t*)&p; memcpy(q,a.payload.data(),a.payload.size()); memcpy(q+FRAGMENT_BYTES,b.payload.data(),b.payload.size()); memcpy(q+2*FRAGMENT_BYTES,d.payload.data(),d.payload.size());
        if(refValid(p)&&p.batchNumber==a.batch) {{ expect.push_back(a.batch); i+=2; }} }} }}
    // device under test
    std::vector<uint32_t> got; c.nextFragment=0; c.assemblingBatch=0; c.ackPending=false;
    for(auto&f:stream){{ std::vector<uint8_t> d(sizeof(FragmentHeader)); FragmentHeader h{{f.magic,f.batch,f.idx,f.cnt}}; memcpy(d.data(),&h,sizeof h); d.insert(d.end(),f.payload.begin(),f.payload.end());
      uint32_t before=c.packetToken; c.handleNotify(nullptr,d.data(),d.size(),f.notify); if(c.packetToken!=before) got.push_back(c.pendingPacket.batchNumber); }}
    ++trials; accepted+=got.size();
    if(got!=expect){{ if(got.size()>expect.size()) ++wrongAccept; else ++missed; if(wrongAccept+missed<=5){{ printf("MISMATCH trial %d: expect=",t); for(auto x:expect)printf("%u ",x); printf(" got="); for(auto x:got)printf("%u ",x); printf("\\n"); }} }} }}
  printf("trials=%ld accepted=%ld wrong_accepts=%ld missed_valid=%ld\\n",trials,accepted,wrongAccept,missed); return (wrongAccept||missed)?1:0; }}
'''
open(os.path.join(out, 't.cpp'), 'w').write(src)
r = subprocess.run(['g++', '-std=c++17', '-O1', '-o', os.path.join(out, 't'), os.path.join(out, 't.cpp')], capture_output=True, text=True)
if r.returncode: print(r.stderr[:3000]); sys.exit(2)
r = subprocess.run([os.path.join(out, 't')], capture_output=True, text=True); print(r.stdout); sys.exit(r.returncode)
