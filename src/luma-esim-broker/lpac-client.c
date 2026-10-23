/* SPDX-License-Identifier: Apache-2.0 */
#include "lpac-client.h"
#include "native-terminal-client.h"
#include <string.h>
#include <unistd.h>

void luma_esim_secret_free (guint8 *data,gsize size) {
  if(!data)return;
  volatile guint8 *p=data;while(size--)*p++=0;
  g_free(data);
}
static void cancelled(GCancellable *cancel,gpointer data) {
  (void)cancel;
  /* Only this exact GSubprocess handle, never process-name/PID search. */
  g_subprocess_force_exit(G_SUBPROCESS(data));
}
static gboolean lpac_arguments(const char *operation,const char *nickname,int input_fd,const char **argv) {
  argv[0]="/usr/bin/lpac";argv[1]="profile";argv[2]=operation;
  argv[3]=argv[4]=argv[5]=NULL;
  if(g_str_equal(operation,"notification-list")){
    if(input_fd>=0 || nickname)return FALSE;
    argv[1]="notification";argv[2]="list";return TRUE;
  }
  if(g_str_equal(operation,"notification-process")){
    if(input_fd>=0 || !nickname || !*nickname || strlen(nickname)>10)return FALSE;
    for(const char *p=nickname;*p;p++)if(!g_ascii_isdigit(*p))return FALSE;
    if(g_ascii_strtoull(nickname,NULL,10)>G_MAXUINT32)return FALSE;
    /* A sequence number is queue metadata, never a carrier/profile secret. */
    argv[1]="notification";argv[2]="process";argv[3]="-r";argv[4]=nickname;
    return TRUE;
  }
  if(g_str_equal(operation,"download"))argv[3]="-A";
  else if(g_str_equal(operation,"enable") || g_str_equal(operation,"disable")){argv[3]="-";argv[4]="0";}
  else if(g_str_equal(operation,"delete"))argv[3]="-";
  else if(g_str_equal(operation,"nickname")){argv[3]="-";argv[4]=nickname?nickname:"";}
  else if(!g_str_equal(operation,"list"))return FALSE;
  return g_str_equal(operation,"list") || input_fd>=0;
}
gboolean luma_esim_lpac_bound(GDBusConnection *bus,const char *token,const char *ims_owner,const char *operation,const char *nickname,int input_fd,
                        GCancellable *cancel,guint8 **output,gsize *size,const LumaMmBinding *expected,LumaMmBinding **observed,GError **error) {
  gboolean ok=FALSE;
  LumaMmTransport *transport=NULL;
  g_autoptr(GSubprocessLauncher) launch=NULL;
  g_autoptr(GSubprocess) child=NULL;
  g_autoptr(GPtrArray) env=g_ptr_array_new();
  guint8 *bytes=g_malloc0(1024*1024);
  gsize used=0;
  gulong cancel_id=0;
  const char *argv[6];
  *output=NULL;*size=0;if(observed)*observed=NULL;
  if(!lpac_arguments(operation,nickname,input_fd,argv))goto out;
  if(g_cancellable_is_cancelled(cancel))goto out;
  transport=expected?luma_mm_transport_open_bound(bus,token,ims_owner,expected,error):
    luma_mm_transport_open_current(bus,token,ims_owner,error);
  if(!transport)goto out;
  launch=g_subprocess_launcher_new(G_SUBPROCESS_FLAGS_STDOUT_PIPE | G_SUBPROCESS_FLAGS_STDERR_SILENCE);
  g_ptr_array_add(env,"PATH=/usr/bin:/bin");g_ptr_array_add(env,"LANG=C.UTF-8");g_ptr_array_add(env,"HOME=/nonexistent");
  g_ptr_array_add(env,"LPAC_HTTP=curl");g_ptr_array_add(env,"LPAC_HTTP_DEBUG=false");g_ptr_array_add(env,"LPAC_APDU_DEBUG=false");
  g_ptr_array_add(env,NULL);
  g_subprocess_launcher_set_environ(launch,(char **)env->pdata);
  g_subprocess_launcher_set_cwd(launch,"/");
  if(transport && !luma_mm_transport_attach(transport,launch))goto out;
  if(input_fd>=0){g_subprocess_launcher_take_stdin_fd(launch,input_fd);input_fd=-1;}
  child=g_subprocess_launcher_spawnv(launch,argv,NULL);
  if(!child)goto out;
  cancel_id=g_cancellable_connect(cancel,G_CALLBACK(cancelled),g_object_ref(child),g_object_unref);
  for(;;) {
    if(used==1024*1024){
      guint8 overflow=0;gssize n=g_input_stream_read(g_subprocess_get_stdout_pipe(child),&overflow,1,cancel,NULL);
      if(n!=0)goto out;
      break;
    }
    gssize n=g_input_stream_read(g_subprocess_get_stdout_pipe(child),bytes+used,
                                MIN((gsize)4096,1024*1024-used),cancel,NULL);
    if(n<0)goto out;
    if(n==0)break;
    used+=(gsize)n;
  }
  if(!g_subprocess_wait_check(child,cancel,NULL))goto out;
  *size=used;*output=bytes;bytes=NULL;ok=TRUE;
out:
  if(input_fd>=0)close(input_fd);
  if(cancel_id)g_cancellable_disconnect(cancel,cancel_id);
  if(!ok && child){g_subprocess_force_exit(child);g_subprocess_wait(child,NULL,NULL);}
  /* Drop any unspawned launcher descriptor before independent native proof. */
  g_clear_object(&launch);
  if(transport){
    GError *terminal_error=NULL;
    luma_mm_transport_abandon_local_fd(transport);
    if(!luma_mm_transport_wait_terminal(transport,&terminal_error)){
      ok=FALSE;luma_esim_secret_free(*output,*size);*output=NULL;*size=0;
      if(error && !*error && terminal_error)g_propagate_error(error,terminal_error);
      else g_clear_error(&terminal_error);
    }
    if(ok&&observed){
      *observed=luma_mm_transport_binding(transport,error);
      if(!*observed){ok=FALSE;luma_esim_secret_free(*output,*size);*output=NULL;*size=0;}
    }
    luma_mm_transport_destroy(transport);
  }
  luma_esim_secret_free(bytes,1024*1024);
  if(!ok && (!error || !*error))g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_FAILED,"The eSIM operation did not complete; refresh profiles before retrying");
  return ok;
}

gboolean luma_esim_lpac_owned(GDBusConnection *bus,const char *token,const char *ims_owner,const char *operation,
 const char *nickname,int input_fd,GCancellable *cancel,guint8 **output,gsize *size,GError **error){
 return luma_esim_lpac_bound(bus,token,ims_owner,operation,nickname,input_fd,cancel,output,size,NULL,NULL,error);
}
