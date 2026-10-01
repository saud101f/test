// Host harness: compiles the REAL CsvStore.h (path rewritten at build time only) against POSIX,
// with syscall wrappers that inject a crash before the N-th mutating syscall.
// Durability model on crash: every file reverts to its content at its last fsync (never-fsynced
// files revert to empty). Renames/creates/ftruncate are treated as immediately durable (FATFS
// writes the directory entry through; NOT proven for real cards - see REVIEW.md).
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <cerrno>
#include <ctime>
#include <string>
#include <map>
#include <vector>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <dirent.h>
#include "esp_timer.h"
#include "device_types.h"   // BatchPacket/TimedSample extracted verbatim from the .ino

static uint32_t esp_random() { return 0x1234abcd; }
struct { uint64_t getEfuseMac() { return 0x00000000beefULL; } } ESP;

static long crashAt = -1; static int tornMode = 0; static long opCount = 0;
static std::map<int,std::string> fdPath; static std::map<std::string,std::string> durable;
static std::string readAll(const std::string&p){FILE*f=fopen(p.c_str(),"rb");std::string s;if(!f)return s;char b[4096];size_t n;while((n=fread(b,1,sizeof b,f)))s.append(b,n);fclose(f);return s;}
static void writeAll(const std::string&p,const std::string&s){FILE*f=fopen(p.c_str(),"wb");if(f){fwrite(s.data(),1,s.size(),f);fclose(f);}}
static std::vector<std::string> listFiles(const char*dir){std::vector<std::string>v;DIR*d=opendir(dir);if(!d)return v;dirent*e;while((e=readdir(d)))if(e->d_name[0]!='.')v.push_back(std::string(dir)+"/"+e->d_name);closedir(d);return v;}
static const char*ROOT=SDROOT;
static void crashNow(){
  for(auto&p:listFiles((std::string(ROOT)+"/hourly").c_str())){ auto it=durable.find(p); writeAll(p,it==durable.end()?std::string():it->second); }
  _exit(77);
}
static bool tick(){ ++opCount; return crashAt>0 && opCount==crashAt; }
static int w_open(const char*p,int fl,int mode=0){ if(tick()) crashNow(); int fd=open(p,fl,mode); if(fd>=0) fdPath[fd]=p; return fd; }
static ssize_t w_write(int fd,const void*b,size_t n){
  if(tick()){ if(tornMode&&n>1){ write(fd,b,n/2); } crashNow(); }
  return write(fd,b,n); }
static int w_fsync(int fd){ if(tick()) crashNow(); int r=fsync(fd); if(r==0) durable[fdPath[fd]]=readAll(fdPath[fd]); return r; }
static int w_close(int fd){ int r=close(fd); fdPath.erase(fd); return r; }
static int w_rename(const char*a,const char*b){ if(tick()) crashNow(); int r=rename(a,b); if(r==0){ auto it=durable.find(a); if(it!=durable.end()){durable[b]=it->second;durable.erase(it);} } return r; }
static int w_ftruncate(int fd,off_t n){ if(tick()) crashNow(); return ftruncate(fd,n); }
#define open w_open
#define write w_write
#define fsync w_fsync
#define close w_close
#define rename w_rename
#define ftruncate w_ftruncate
#include "CsvStore_host.h"
#undef open
#undef write
#undef fsync
#undef close
#undef rename
#undef ftruncate

static BatchPacket mk(int dev,uint32_t batch){
  BatchPacket p; memset(&p,0,sizeof p); p.version=3; p.deviceID=dev; p.bootID=0x1111000000000000ULL+dev; p.batchNumber=batch; p.sampleCount=25; p.bufferCount=25;
  for(int i=0;i<25;++i){ p.samples[i].sequence=batch*25+i; p.samples[i].acquiredUs=1000000ULL*batch+40000ULL*i; p.samples[i].accel.x=dev*100+i; p.samples[i].accel.y=-(int)batch; p.samples[i].accel.z=1000; p.samples[i].gyroX=i; p.samples[i].gyroY=-i; p.samples[i].gyroZ=(int)batch; }
  return p; }
// Scenario: 12 batches alternating devices 1,2 ; clock crosses an hour boundary every 4 batches.
static const int N=12;
static time_t T0=1790000000; // 2026-09-21 -> after 1767225600 so wallclock "synced"
static time_t tOf(int i){ return T0 + (i/4)*3600 + i; }
static int dev(int i){ return 1+(i%2); }
int main(int argc,char**argv){
  std::string mode=argv[1];
  CsvStore s;
  if(mode=="phase1"){ crashAt=atol(argv[2]); tornMode=atoi(argv[3]); }
  if(mode=="phase1"||mode=="count"){
    if(!s.begin()){ printf("BEGIN_FAIL %s\n",s.error); return 2; }
    FILE*ack=fopen((std::string(ROOT)+"/acked.txt").c_str(),"w");
    for(int i=0;i<N;++i){ BatchPacket p=mk(dev(i),i+1); bool ok=s.append(p,tOf(i),i*1000);
      if(!ok){ printf("APPEND_FAIL %d %s\n",i+1,s.error); return 3; }
      fprintf(ack,"%d\n",i+1); fflush(ack); }
    fclose(ack);
    printf("OPS %ld\n",opCount); return 0; }
  if(mode=="phase2"){ // fresh boot: recover, collar resends last acked batch (lost-ACK case) then everything not yet acked
    int lastAcked=atoi(argv[2]);
    if(!s.begin()){ printf("BEGIN_FAIL %s\n",s.error); return 2; }
    int start = lastAcked>0 ? lastAcked-1 : 0; // resend the last ACKed batch too (ACK lost with the crash)
    for(int i=start;i<N;++i){ BatchPacket p=mk(dev(i),i+1); if(!s.append(p,tOf(i)+7200,(i+100)*1000)){ printf("APPEND_FAIL %d %s\n",i+1,s.error); return 3; } }
    if(!s.seal()){ printf("SEAL_FAIL %s\n",s.error); return 4; }
    return 0; }
}
