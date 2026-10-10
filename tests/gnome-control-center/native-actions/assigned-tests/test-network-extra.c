/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "cc-luma-live.h"
#include <NetworkManager.h>
#include <sys/prctl.h>
#include <signal.h>
extern const CcLumaAdapter cc_luma_adapter_network;
static gboolean ready;
static guint errors;
void cc_luma_live_ready (CcLumaFixture *m, const char *a) { ready = TRUE; }
static void spin (guint ms) { gint64 end = g_get_monotonic_time () + ms * 1000; do { while (g_main_context_iteration (NULL, FALSE)); g_usleep (1000); } while (g_get_monotonic_time () < end); }
static gboolean writer (CcLumaFixture *m, const char *key, JsonNode *v, gpointer state, GError **error) { return cc_luma_adapter_network.write (state,key,v,error); }
static void reported (CcLumaFixture *m, const GError *error, gpointer unused) { g_assert_nonnull(error); errors++; }
static GVariant *call (GDBusConnection *bus, const char *path, const char *iface, const char *method, GVariant *args) { g_autoptr(GError) e=NULL; GVariant *v=g_dbus_connection_call_sync(bus,NM_DBUS_SERVICE,path,iface,method,args,NULL,G_DBUS_CALL_FLAGS_NONE,5000,NULL,&e);g_assert_no_error(e);g_assert_nonnull(v);return v; }
static void text (CcLumaFixture *m, const char *key, const char *value) { g_autoptr(GError) e=NULL;g_autoptr(JsonNode) v=json_node_init_string(json_node_alloc(),value);g_assert_true(cc_luma_fixture_write(m,key,v,&e));g_assert_no_error(e); }
static void boolean (CcLumaFixture *m, const char *key, gboolean value) { g_autoptr(GError) e=NULL;g_autoptr(JsonNode) v=json_node_init_boolean(json_node_alloc(),value);g_assert_true(cc_luma_fixture_write(m,key,v,&e));g_assert_no_error(e); }
static void invalid (CcLumaFixture *m, const char *key, JsonNode *v) { g_autoptr(GError) e=NULL;g_assert_false(cc_luma_fixture_write(m,key,v,&e));g_assert_error(e,G_IO_ERROR,G_IO_ERROR_FAILED); }
static const char *read_text(CcLumaFixture *m,const char *key){return json_node_get_string(cc_luma_fixture_get(m,key));}
static void child_setup(gpointer unused){prctl(PR_SET_PDEATHSIG,SIGTERM);}
int main (int argc,char **argv) {
 g_test_init(&argc,&argv,NULL);
 g_autoptr(GSubprocessLauncher) launcher=g_subprocess_launcher_new(G_SUBPROCESS_FLAGS_STDOUT_PIPE);g_subprocess_launcher_set_child_setup(launcher,child_setup,NULL,NULL);
 g_autoptr(GSubprocess) daemon=g_subprocess_launcher_spawn(launcher,NULL,"dbus-daemon","--session","--nofork","--print-address=1",NULL);
 g_autoptr(GDataInputStream) stream=g_data_input_stream_new(g_subprocess_get_stdout_pipe(daemon));g_autofree char *address=g_data_input_stream_read_line(stream,NULL,NULL,NULL);
 g_setenv("DBUS_SYSTEM_BUS_ADDRESS",address,TRUE);g_setenv("DBUS_SESSION_BUS_ADDRESS",address,TRUE);
 g_autoptr(GSubprocess) service=g_subprocess_new(G_SUBPROCESS_FLAGS_STDIN_PIPE,NULL,"python3",TEST_NM_SERVICE,NULL);g_assert_nonnull(service);spin(500);
 g_autoptr(GError) error=NULL;g_autoptr(GDBusConnection) bus=g_bus_get_sync(G_BUS_TYPE_SYSTEM,NULL,&error);g_assert_no_error(error);
 g_autoptr(GVariant) wifi=call(bus,NM_DBUS_PATH,"org.freedesktop.NetworkManager.LibnmGlibTest","AddWifiDevice",g_variant_new("(s)","audit-wifi"));
 g_autoptr(JsonParser) parser=json_parser_new();g_assert_true(json_parser_load_from_data(parser,"{\"version\":1,\"settings\":{},\"data\":{},\"pages\":[]}",-1,NULL));
 g_autoptr(CcLumaFixture) model=cc_luma_fixture_new_live(json_parser_get_root(parser));gpointer state=cc_luma_adapter_network.start(model);cc_luma_fixture_set_writer(model,writer,state);cc_luma_live_verify_network_keys(model);g_signal_connect(model,"write-error",G_CALLBACK(reported),NULL);
 for(guint i=0;i<100&&!ready;i++) { spin(20); }
 g_assert_true(ready);
 const char *keys[]={"hotspot","hsName","hsPass","hsBand","eth",NULL};for(guint i=0;keys[i];i++){g_assert_true(cc_luma_fixture_writable(model,keys[i]));g_assert_true(cc_luma_fixture_key_verified(model,keys[i]));}
 g_autoptr(JsonNode) bad_boolean=json_node_init_string(json_node_alloc(),"on"),bad_text=json_node_init_boolean(json_node_alloc(),TRUE),bad_band=json_node_init_string(json_node_alloc(),"6");
 invalid(model,"hotspot",bad_boolean);invalid(model,"eth",bad_boolean);invalid(model,"hsName",bad_text);invalid(model,"hsPass",bad_text);invalid(model,"hsBand",bad_band);
 g_autoptr(JsonNode) on=json_node_init_boolean(json_node_alloc(),TRUE);invalid(model,"eth",on);g_assert_false(json_node_get_boolean(cc_luma_fixture_get(model,"eth")));
 text(model,"hsName","Luma hotspot audit");text(model,"hsPass","auditPassword42");text(model,"hsBand","2");boolean(model,"hotspot",TRUE);spin(700);
 g_assert_cmpuint(errors,==,0);g_assert_true(json_node_get_boolean(cc_luma_fixture_get(model,"hotspot")));g_assert_cmpstr(read_text(model,"hsName"),==,"Luma hotspot audit");g_assert_cmpstr(read_text(model,"hsPass"),==,"auditPassword42");g_assert_cmpstr(read_text(model,"hsBand"),==,"2");
 text(model,"hsName","Luma hotspot renamed");text(model,"hsPass","savedPassword42");text(model,"hsBand","5");spin(1250);
 g_assert_cmpuint(errors,==,0);g_assert_cmpstr(read_text(model,"hsName"),==,"Luma hotspot renamed");g_assert_cmpstr(read_text(model,"hsPass"),==,"savedPassword42");g_assert_cmpstr(read_text(model,"hsBand"),==,"5");
 g_autoptr(NMClient) native=nm_client_new(NULL,&error);g_assert_no_error(error);const GPtrArray *connections=nm_client_get_connections(native);g_assert_cmpuint(connections->len,==,1);NMConnection *c=g_ptr_array_index(connections,0);NMSettingWireless *w=nm_connection_get_setting_wireless(c);g_assert_nonnull(w);g_assert_cmpstr(nm_setting_wireless_get_mode(w),==,"ap");g_assert_cmpstr(nm_setting_wireless_get_band(w),==,"a");g_assert_cmpstr(nm_setting_ip_config_get_method(nm_connection_get_setting_ip4_config(c)),==,"shared");
 g_autoptr(GVariant) settings=call(bus,nm_object_get_path(NM_OBJECT(c)),NM_DBUS_INTERFACE_SETTINGS_CONNECTION,"GetSettings",NULL);g_autoptr(GVariant) hash=g_variant_get_child_value(settings,0);g_autoptr(GVariant) sec=g_variant_lookup_value(hash,"802-11-wireless-security",G_VARIANT_TYPE("a{sv}"));const char *psk=NULL;g_assert_true(g_variant_lookup(sec,"psk","&s",&psk));g_assert_cmpstr(psk,==,"savedPassword42");
 g_print("PASS native hotspot AddAndActivate/Update2 persistent SSID/password/band and owner readback; five explicit qualified keys; invalid types/band and absent Ethernet rejected\n");
 g_autoptr(GVariant) eth=call(bus,NM_DBUS_PATH,"org.freedesktop.NetworkManager.LibnmGlibTest","AddWiredDevice",g_variant_new("(ss@as)","audit-eth","52:54:00:00:00:99",g_variant_new_strv(NULL,0)));spin(300);
 boolean(model,"eth",TRUE);spin(500);g_assert_cmpuint(errors,==,1);g_assert_false(json_node_get_boolean(cc_luma_fixture_get(model,"eth")));g_assert_cmpstr(read_text(model,"netError"),!=,"");
 g_print("PASS native Ethernet activation denial reports retained in-pane write-error and refreshes actual disconnected state (test service does not implement automatic wired profile selection)\n");
 cc_luma_adapter_network.stop(state);spin(100);g_output_stream_close(g_subprocess_get_stdin_pipe(service),NULL,NULL);g_assert_true(g_subprocess_wait_check(service,NULL,&error));g_assert_no_error(error);g_subprocess_send_signal(daemon,SIGTERM);g_subprocess_wait(daemon,NULL,NULL);return 0;
}
