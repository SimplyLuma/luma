/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "cc-luma-live-windows.c"
static guint errors,calls;static gboolean refused;
void cc_luma_live_ready(CcLumaFixture *m,const char *a) {}
void cc_luma_live_publish_dock(CcLumaFixture *m) {}
void cc_luma_live_desk_dump_watch(CcLumaFixture *m) {}
gboolean cc_luma_windows_validate(CcLumaFixture *m){return FALSE;}
gboolean cc_luma_night_validate(CcLumaFixture *m){return FALSE;}
gboolean cc_luma_pointer_validate(CcLumaFixture *m){return FALSE;}
gboolean cc_luma_a11y_validate(CcLumaFixture *m){return FALSE;}
static void got_error(CcLumaFixture *m,const GError *e,gpointer data){errors++;}
static const char *xml="<node><interface name='org.gnome.Shell.Extensions'><method name='EnableExtension'><arg type='s' direction='in'/><arg type='b' direction='out'/></method><method name='DisableExtension'><arg type='s' direction='in'/><arg type='b' direction='out'/></method></interface></node>";
static void method(GDBusConnection *b,const char *s,const char *p,const char *i,const char *method,GVariant *params,GDBusMethodInvocation *inv,gpointer data){
 const char *uuid;g_variant_get(params,"(&s)",&uuid);g_assert_cmpstr(uuid,==,TILING_UUID);calls++;
 if(!refused){g_autoptr(GSettings) shell=g_settings_new("org.gnome.shell");const char *on[]={TILING_UUID,NULL};const char *off[]={NULL};g_settings_set_strv(shell,"enabled-extensions",g_str_equal(method,"EnableExtension")?on:off);g_settings_set_strv(shell,"disabled-extensions",off);}
 g_dbus_method_invocation_return_value(inv,g_variant_new("(b)",!refused));
}
static const GDBusInterfaceVTable vt={method,NULL,NULL,{0}};
static void settle(void){gint64 until=g_get_monotonic_time()+200000;do{while(g_main_context_iteration(NULL,FALSE));g_usleep(1000);}while(g_get_monotonic_time()<until);}
static gboolean writer(CcLumaFixture *m,const char *k,JsonNode *v,gpointer data,GError **e){return windows_write(data,k,v,e);}
int main(void){
 g_setenv("GSETTINGS_BACKEND","memory",TRUE);g_autoptr(GError) error=NULL;
 g_autoptr(GDBusConnection) bus=g_bus_get_sync(G_BUS_TYPE_SESSION,NULL,&error);g_assert_no_error(error);
 g_autoptr(GDBusNodeInfo) node=g_dbus_node_info_new_for_xml(xml,&error);g_assert_no_error(error);
 g_assert_cmpuint(g_dbus_connection_register_object(bus,"/org/gnome/Shell",node->interfaces[0],&vt,NULL,NULL,&error),>,0);
 g_autoptr(GVariant) own=g_dbus_connection_call_sync(bus,"org.freedesktop.DBus","/org/freedesktop/DBus","org.freedesktop.DBus","RequestName",g_variant_new("(su)","org.gnome.Shell",0),NULL,0,1000,NULL,&error);g_assert_no_error(error);
 g_autoptr(JsonParser) parser=json_parser_new();g_assert_true(json_parser_load_from_data(parser,"{\"settings\":{},\"data\":{}}",-1,NULL));
 g_autoptr(CcLumaFixture) m=cc_luma_fixture_new_live(json_parser_get_root(parser));Windows *w=windows_start(m);g_assert_nonnull(w->tiling);cc_luma_fixture_set_writer(m,writer,w);g_signal_connect(m,"write-error",G_CALLBACK(got_error),NULL);
 for(guint i=0;i<G_N_ELEMENTS(rows);i++)if(cc_luma_gs_table_covers(w->table,rows[i].key))g_assert_true(cc_luma_fixture_key_verified(m,rows[i].key));
 g_autoptr(JsonNode) on=json_node_init_boolean(json_node_alloc(),TRUE);g_assert_true(cc_luma_fixture_write(m,"tile",on,&error));settle();g_assert_true(tiling_on(w));
 g_autoptr(JsonNode) gap=json_node_init_int(json_node_alloc(),12);g_assert_true(cc_luma_fixture_write(m,"gap",gap,&error));g_assert_cmpuint(g_settings_get_uint(w->tiling,"inner-gaps"),==,12);
 g_assert_true(cc_luma_fixture_write(m,"edgeTile",on,&error));g_assert_true(g_settings_get_boolean(w->tiling,"active-screen-edges"));
 windows_stop(w);w=windows_start(m);cc_luma_fixture_set_writer(m,writer,w);g_assert_true(tiling_on(w));g_assert_cmpuint(g_settings_get_uint(w->tiling,"inner-gaps"),==,12);
 g_assert_true(g_settings_set_boolean(w->shell,"disable-user-extensions",TRUE));settle();
 g_assert_false(tiling_on(w));g_assert_false(json_node_get_boolean(cc_luma_fixture_get(m,"tile")));
 guint before=calls;g_assert_false(cc_luma_fixture_write(m,"tile",on,&error));g_assert_error(error,G_IO_ERROR,G_IO_ERROR_PERMISSION_DENIED);g_clear_error(&error);
 g_assert_cmpuint(calls,==,before);g_assert_true(g_settings_get_boolean(w->shell,"disable-user-extensions"));
 g_assert_true(g_settings_set_boolean(w->shell,"disable-user-extensions",FALSE));settle();g_assert_true(tiling_on(w));
 refused=TRUE;g_autoptr(JsonNode) off=json_node_init_boolean(json_node_alloc(),FALSE);g_assert_true(cc_luma_fixture_write(m,"tile",off,&error));settle();g_assert_true(tiling_on(w));g_assert_cmpuint(errors,==,1);
 g_autoptr(JsonNode) invalid=json_node_init_int(json_node_alloc(),-1);g_assert_false(cc_luma_fixture_write(m,"gap",invalid,&error));g_assert_error(error,G_IO_ERROR,G_IO_ERROR_INVALID_ARGUMENT);g_clear_error(&error);
 windows_stop(w);settle();g_print("PASS native tiling Shell call, gap/edges GSettings, reopen, global-pause refusal without changing user pause, refused Shell response and invalid gap; %u calls\n",calls);return 0;
}
