/* SPDX-License-Identifier: MIT
 * Implements lpac's existing euicc_apdu_interface. SOURCE candidate only.
 * Interface provenance: lpac 3ff35594ec15062a3ed10c3da1c26eb0a13390b8,
 * euicc/interface.h and driver/apdu/stdio.c; framing implementation is new.
 */
#define _GNU_SOURCE
#include "luma-owned-apdu-wire.h"
#include <driver.h>
#include <euicc/interface.h>
#include <euicc/euicc.h>
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
struct owned_fd { int fd; uint32_t sequence; uint8_t channel; int broken; int64_t deadline; };
static void break_transport(struct owned_fd *s) {
    s->broken=1;
    if(s->fd>=0){int fd=s->fd;s->fd=-1;close(fd);}
    /* Keep channel state: EOF is not a native CLOSE acknowledgement. */
}
static int64_t milliseconds(void) {
    struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t))return -1;
    return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000;
}
static int ready(struct owned_fd *s,short events,int64_t deadline) {
    for(;;){int64_t now=milliseconds();if(now<0||now>=deadline)return -1;
        struct pollfd p={s->fd,events,0};int r=poll(&p,1,(int)(deadline-now));
        if(r<0&&errno==EINTR)continue;
        return r==1&&(p.revents&events)&&!(p.revents&(POLLERR|POLLNVAL))?0:-1;}
}
static int exchange(struct owned_fd *s,uint32_t op,const uint8_t *data,uint32_t len,
                    uint8_t **out,uint32_t *out_len) {
    uint8_t header[LUMA_APDU_HEADER],*reply=NULL;int result=-1;
    if(out) *out=NULL;
    if(out_len) *out_len=0;
    int64_t now=milliseconds();
    if(s->broken||s->fd<0||len>LUMA_APDU_MAX||(len&&!data)||now<0||now>=s->deadline||
       s->sequence>=LUMA_APDU_CALLS)goto done;
    int64_t deadline=now+10000;if(deadline>s->deadline)deadline=s->deadline;
    uint32_t sequence=++s->sequence;
    luma_wire_put(header,LUMA_APDU_MAGIC);luma_wire_put(header+4,sequence);
    luma_wire_put(header+8,op);luma_wire_put(header+12,len);
    struct iovec iov[2]={{header,sizeof header},{(void*)data,len}};
    struct msghdr message={0};message.msg_iov=iov;message.msg_iovlen=len?2:1;
    if(ready(s,POLLOUT,deadline)||sendmsg(s->fd,&message,MSG_NOSIGNAL|MSG_DONTWAIT)!=(ssize_t)(sizeof header+len))goto done;
    reply=malloc(LUMA_APDU_HEADER+LUMA_APDU_MAX);if(!reply)goto done;
    if(ready(s,POLLIN,deadline))goto done;
    struct iovec rx={reply,LUMA_APDU_HEADER+LUMA_APDU_MAX};message=(struct msghdr){0};message.msg_iov=&rx;message.msg_iovlen=1;
    ssize_t n=recvmsg(s->fd,&message,MSG_DONTWAIT);
    if(n<(ssize_t)LUMA_APDU_HEADER||(message.msg_flags&(MSG_TRUNC|MSG_CTRUNC))||
       luma_wire_get(reply)!=LUMA_APDU_MAGIC||luma_wire_get(reply+4)!=sequence||
       luma_wire_get(reply+8)!=0||luma_wire_get(reply+12)!=(uint32_t)n-LUMA_APDU_HEADER)goto done;
    uint32_t size=(uint32_t)n-LUMA_APDU_HEADER;
    if(out&&out_len){*out=malloc(size?size:1);if(!*out)goto done;memcpy(*out,reply+LUMA_APDU_HEADER,size);*out_len=size;}
    else if(size)goto done;
    result=0;
done:
    if(reply){volatile uint8_t *p=reply;for(size_t i=0;i<LUMA_APDU_HEADER+LUMA_APDU_MAX;i++)p[i]=0;free(reply);}
    if(result)break_transport(s);
    return result;
}
static struct owned_fd *state(struct euicc_ctx *ctx){return ctx->apdu.interface->userdata;}
static int connect_owned(struct euicc_ctx *ctx) {
    struct owned_fd *s=state(ctx);const char *text=getenv("LPAC_APDU_LUMA_FD");char *end=NULL;
    if(!text||!text[0]||s->fd>=0) return -1;
    errno=0;
    long fd=strtol(text,&end,10);
    if(errno||*end||fd<3||fd>1024)return -1;
    int type=0; socklen_t size=sizeof type;struct sockaddr_storage address;size=sizeof address;
    if(getsockname((int)fd,(struct sockaddr*)&address,&size)||address.ss_family!=AF_UNIX)return -1;
    size=sizeof type;if(getsockopt((int)fd,SOL_SOCKET,SO_TYPE,&type,&size)||type!=SOCK_SEQPACKET)return -1;
    struct ucred peer;size=sizeof peer;
    if(getsockopt((int)fd,SOL_SOCKET,SO_PEERCRED,&peer,&size)||size!=sizeof peer||peer.uid!=0||peer.pid<=1)return -1;
    s->fd=fcntl((int)fd,F_DUPFD_CLOEXEC,3);if(s->fd<0)return -1;
    close((int)fd);
    int64_t now=milliseconds();
    if(now<0){close(s->fd);s->fd=-1;s->broken=1;return -1;}
    s->deadline=now+300000;
    int rc=exchange(s,LUMA_CONNECT,NULL,0,NULL,NULL);
    if(rc){close(s->fd);s->fd=-1;}
    return rc;
}
static int open_owned(struct euicc_ctx *ctx,const uint8_t *aid,uint8_t len) {
    struct owned_fd *s=state(ctx);uint8_t *reply=NULL;uint32_t size=0;
    if(!aid||!len||len>32||s->channel)return -1;
    int rc=exchange(s,LUMA_OPEN,aid,len,&reply,&size);
    if(rc||size!=1||reply[0]==0||reply[0]>3){free(reply);break_transport(s);return -1;}
    s->channel=reply[0];free(reply);return s->channel;
}
static void close_owned(struct euicc_ctx *ctx,uint8_t channel) {
    struct owned_fd *s=state(ctx);if(!s->channel||channel!=s->channel){s->broken=1;return;}
    if(!exchange(s,LUMA_CLOSE,&channel,1,NULL,NULL))s->channel=0;
}
static int transmit_owned(struct euicc_ctx *ctx,uint8_t **rx,uint32_t *len,const uint8_t *tx,uint32_t tx_len) {
    struct owned_fd *s=state(ctx);
    if(rx) *rx=NULL;
    if(len) *len=0;
    if(!s->channel||!rx||!len||!tx||tx_len<4||(tx[0]&0x4c)||(tx[0]&0x03)!=s->channel) return -1;
    int rc=exchange(s,LUMA_TRANSMIT,tx,tx_len,rx,len);
    if(!rc&&*len<2){free(*rx);*rx=NULL;*len=0;break_transport(s);return -1;}
    return rc;
}
static void disconnect_owned(struct euicc_ctx *ctx) {
    struct owned_fd *s=state(ctx);if(s->fd<0)return;
    if(s->channel)close_owned(ctx,s->channel);
    if(!s->broken)exchange(s,LUMA_DISCONNECT,NULL,0,NULL,NULL);
    close(s->fd);s->fd=-1;
}
static int init_owned(struct euicc_apdu_interface *iface) {
    struct owned_fd *s=calloc(1,sizeof *s);if(!s)return -1;s->fd=-1;
    memset(iface,0,sizeof *iface);iface->connect=connect_owned;iface->disconnect=disconnect_owned;
    iface->logic_channel_open=open_owned;iface->logic_channel_close=close_owned;
    iface->transmit=transmit_owned;iface->userdata=s;return 0;
}
static void fini_owned(struct euicc_apdu_interface *iface) {
    struct owned_fd *s=iface->userdata;
    if(!s) return;
    if(s->fd>=0) close(s->fd);
    volatile unsigned char *p=(volatile unsigned char*)s;
    for(size_t i=0;i<sizeof *s;i++) p[i]=0;
    free(s);iface->userdata=NULL;
}
DRIVER_INTERFACE={.type=DRIVER_APDU,.name="luma_mm_fd",.init=(int(*)(void*))init_owned,.main=NULL,.fini=(void(*)(void*))fini_owned};
