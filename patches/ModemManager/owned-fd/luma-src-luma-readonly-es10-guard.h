/* SPDX-License-Identifier: MIT
 * Read-only diagnostic framing derived from pinned LPAC 3ff35594 euicc.c,
 * es10b_get_euicc_info_r and es10c_get_profiles_info. No profile mutation. */
#ifndef LUMA_READONLY_ES10_GUARD_H
#define LUMA_READONLY_ES10_GUARD_H
#include <stddef.h>
#include <stdint.h>
typedef struct { unsigned calls,seen,reply_limit; uint8_t expected_le; int pending,more; } LumaReadonlyEs10;
static int luma_readonly_es10_request(LumaReadonlyEs10 *s,unsigned channel,const uint8_t *p,size_t n) {
 unsigned bit=0;
 if(!s||!p||channel<1||channel>3||n<5||p[0]!=(uint8_t)(0x80|channel)||s->pending||s->calls>=32)return 0;
 if(n==8&&p[1]==0xe2&&p[2]==0x91&&p[3]==0&&p[4]==3&&p[5]==0xbf&&p[7]==0&&!s->more){
  if(p[6]==0x20)bit=1;else if(p[6]==0x2d)bit=2;else return 0;
  if(s->seen&bit)return 0;
  s->seen|=bit;s->reply_limit=256;
 }else if(!(n==5&&p[1]==0xc0&&p[2]==0&&p[3]==0&&s->more&&p[4]==s->expected_le))return 0;
 if(!bit)s->reply_limit=p[4]?p[4]:256;
 s->pending=1;s->more=0;s->calls++;return 1;
}
static int luma_readonly_es10_response(LumaReadonlyEs10 *s,const uint8_t *p,size_t n) {
 if(!s||!p||!s->pending||n<2||n>s->reply_limit+2)return 0;
 s->pending=0;
 if(p[n-2]==0x61){s->more=1;s->expected_le=p[n-1];return 1;}
 if(p[n-2]==0x90&&p[n-1]==0){s->more=0;return 1;}
 return 0;
}
#endif
