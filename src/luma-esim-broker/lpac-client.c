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
gboolean luma_esim_lpac_bound(GDBusConnection *bus,const char *token,const char *ims_owner,const char *operation,const char *nickname,int input_fd,
                        GCancellable *cancel,guint8 **output,gsize *size,const LumaMmBinding *expected,LumaMmBinding **observed,GError **error) {
  gboolean ok=FALSE;
  LumaMmTransport *transport=NULL;
  g_autoptr(GSubprocessLauncher) launch=NULL;
  g_autoptr(GSubprocess) child=NULL;
  guint8 *bytes=g_malloc0(1024*1024);
  gsize used=0;
  gulong cancel_id=0;
  const char *argv[]={"/usr/bin/lpac","profile",operation,NULL,NULL,NULL};
  *output=NULL;*size=0;if(observed)*observed=NULL;
  if(g_str_equal(operation,"download"))argv[3]="-A";
  else if(g_str_equal(operation,"enable") || g_str_equal(operation,"disable")){argv[3]="-";argv[4]="0";}
  else if(g_str_equal(operation,"delete"))argv[3]="-";
  else if(g_str_equal(operation,"nickname")){argv[3]="-";argv[4]=nickname?nickname:"";}
  else if(!g_str_equal(operation,"list"))goto out;
  if(!g_str_equal(operation,"list") && input_fd<0)goto out;
  if(g_cancellable_is_cancelled(cancel))goto out;
  transport=expected?luma_mm_transport_open_bound(bus,token,ims_owner,expected,error):
    luma_mm_transport_open_current(bus,token,ims_owner,error);
  if(!transport)goto out;
  launch=g_subprocess_launcher_new(G_SUBPROCESS_FLAGS_STDOUT_PIPE | G_SUBPROCESS_FLAGS_STDERR_SILENCE);
  char *env[]={"PATH=/usr/bin:/bin","LANG=C.UTF-8","HOME=/nonexistent", "LPAC_APDU=qmi_qrtr",
    "LPAC_APDU_QMI_UIM_SLOT=2","LPAC_HTTP=curl","LPAC_HTTP_DEBUG=false","LPAC_APDU_DEBUG=false",NULL};
  g_subprocess_launcher_set_environ(launch,env);
  g_subprocess_launcher_set_cwd(launch,"/");
  if(!luma_mm_transport_attach(transport,launch))goto out;
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
