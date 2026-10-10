/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "cc-luma-live-privacy.c"
gboolean cc_luma_night_validate(CcLumaFixture *m) {return FALSE;}
gboolean cc_luma_pointer_validate(CcLumaFixture *m) {return FALSE;}
gboolean cc_luma_a11y_validate(CcLumaFixture *m) {return FALSE;}
static guint calls, error_count;
static void got_error(CcLumaFixture *m,const GError *e,gpointer data){g_assert_nonnull(e);error_count++;}
static gboolean values[3]={TRUE,TRUE,TRUE}, refuse_next;
void cc_luma_live_ready(CcLumaFixture *model,const char *adapter) {}
gboolean cc_luma_live_publish_app(CcLumaFixture *model,const char *id,GAppInfo *info) {return info && g_app_info_get_name(info);}
static const char *xml="<node><interface name='org.freedesktop.impl.portal.PermissionStore'><method name='Lookup'><arg type='s' direction='in'/><arg type='s' direction='in'/><arg type='a{sas}' direction='out'/><arg type='v' direction='out'/></method><method name='SetPermission'><arg type='s' direction='in'/><arg type='b' direction='in'/><arg type='s' direction='in'/><arg type='s' direction='in'/><arg type='as' direction='in'/></method></interface></node>";
static void method(GDBusConnection *c,const char *sender,const char *path,const char *interface,const char *name,GVariant *params,GDBusMethodInvocation *inv,gpointer data)
{
 const char *table,*id;g_variant_get_child(params,0,"&s",&table);
 g_variant_get_child(params,g_str_equal(name,"Lookup")?1:2,"&s",&id);
 guint s=g_str_equal(table,"location")?0:g_str_equal(id,"camera")?1:2;
 if(g_str_equal(name,"Lookup")){
  GVariantBuilder b;g_variant_builder_init(&b,G_VARIANT_TYPE("a{sas}"));
  const char *loc[]={values[s]?"EXACT":"NONE","0",NULL};const char *dev[]={values[s]?"yes":"no",NULL};
  g_variant_builder_add(&b,"{s^as}","org.example.SettingsProbe",s==0?loc:dev);
  g_dbus_method_invocation_return_value(inv,g_variant_new("(a{sas}v)",&b,g_variant_new_string("")));
 }else{
  const char *app;g_variant_get_child(params,3,"&s",&app);g_assert_cmpstr(app,==,"org.example.SettingsProbe");
  g_autoptr(GVariant) perms=g_variant_get_child_value(params,4);g_auto(GStrv) val=g_variant_dup_strv(perms,NULL);
  g_assert_cmpstr(val[0],!=,NULL);calls++;
  if(refuse_next){refuse_next=FALSE;g_dbus_method_invocation_return_dbus_error(inv,"org.freedesktop.DBus.Error.AccessDenied","Test policy refusal");return;}
  values[s]=s==0?g_str_equal(val[0],"EXACT"):g_str_equal(val[0],"yes");
  if(s==0)g_assert_cmpstr(val[1],==,"0");
  g_dbus_method_invocation_return_value(inv,g_variant_new("()"));
 }
}
static const GDBusInterfaceVTable vtable={method,NULL,NULL,{0}};
static void settle(void){gint64 until=g_get_monotonic_time()+300000;do{while(g_main_context_iteration(NULL,FALSE));g_usleep(1000);}while(g_get_monotonic_time()<until);}
static gboolean writer(CcLumaFixture *m,const char *k,JsonNode *v,gpointer p,GError **e){return privacy_write(p,k,v,e);}
int main(void)
{
 g_setenv("GSETTINGS_BACKEND","memory",TRUE);
 g_autoptr(GError) error=NULL;
 g_autoptr(GDBusConnection) bus=g_bus_get_sync(G_BUS_TYPE_SESSION,NULL,&error);g_assert_no_error(error);
 g_autoptr(GDBusNodeInfo) node=g_dbus_node_info_new_for_xml(xml,&error);g_assert_no_error(error);
 g_assert_cmpuint(g_dbus_connection_register_object(bus,"/org/freedesktop/impl/portal/PermissionStore",node->interfaces[0],&vtable,NULL,NULL,&error),>,0);g_assert_no_error(error);
 g_autoptr(GVariant) own=g_dbus_connection_call_sync(bus,"org.freedesktop.DBus","/org/freedesktop/DBus","org.freedesktop.DBus","RequestName",g_variant_new("(su)","org.freedesktop.impl.portal.PermissionStore",0),NULL,0,1000,NULL,&error);g_assert_no_error(error);
 g_autoptr(JsonParser) parser=json_parser_new();g_assert_true(json_parser_load_from_data(parser,"{\"settings\":{},\"data\":{}}",-1,NULL));
 g_autoptr(CcLumaFixture) m=cc_luma_fixture_new_live(json_parser_get_root(parser));Privacy *p=privacy_start(m);cc_luma_fixture_set_writer(m,writer,p);g_signal_connect(m,"write-error",G_CALLBACK(got_error),NULL);settle();
 for(guint s=0;s<3;s++){
  g_autofree char *key=g_strconcat(stores[s].key,".org-example-settingsprobe",NULL);g_assert_true(cc_luma_fixture_key_verified(m,key));
  g_autoptr(JsonNode) off=json_node_init_boolean(json_node_alloc(),FALSE);g_assert_true(cc_luma_fixture_write(m,key,off,&error));g_assert_no_error(error);settle();
  g_assert_false(values[s]);g_assert_false(json_node_get_boolean(cc_luma_fixture_get(m,key)));
  g_autoptr(JsonNode) invalid=json_node_init_string(json_node_alloc(),"yes");g_assert_false(cc_luma_fixture_write(m,key,invalid,&error));g_assert_error(error,G_IO_ERROR,G_IO_ERROR_INVALID_ARGUMENT);g_clear_error(&error);
  g_print("PASS known real portal id, service readback and type-refusal %s\n",key);
 }
 refuse_next=TRUE;g_autoptr(JsonNode) on=json_node_init_boolean(json_node_alloc(),TRUE);
 g_assert_true(cc_luma_fixture_write(m,"appCam.org-example-settingsprobe",on,&error));settle();g_assert_cmpuint(error_count,==,1);g_assert_false(values[1]);g_assert_false(json_node_get_boolean(cc_luma_fixture_get(m,"appCam.org-example-settingsprobe")));
 g_assert_false(privacy_write(p,"appCam.unknown",on,&error));g_assert_error(error,G_IO_ERROR,G_IO_ERROR_NOT_FOUND);g_clear_error(&error);
 g_assert_true(cc_luma_fixture_write(m,"appMic.org-example-settingsprobe",on,&error));privacy_stop(p);settle();g_print("PASS permission refusal restores service value, unknown app rejected, dispose; %u service writes\n",calls);return 0;
}
