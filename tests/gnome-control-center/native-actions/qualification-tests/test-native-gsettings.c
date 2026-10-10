/* SPDX-License-Identifier: GPL-2.0-or-later */
#define G_SETTINGS_ENABLE_BACKEND
#include <gio/gsettingsbackend.h>
#include "cc-luma-live-gsettings.c"
typedef struct { GSettingsBackend parent; } LockedBackend;
typedef struct { GSettingsBackendClass parent; } LockedBackendClass;
G_DEFINE_TYPE (LockedBackend, locked_backend, G_TYPE_SETTINGS_BACKEND)
static GVariant *locked_read(GSettingsBackend *b,const char *k,const GVariantType *t,gboolean d) {return NULL;}
static gboolean locked_writable(GSettingsBackend *b,const char *k) {return FALSE;}
static void locked_backend_class_init(LockedBackendClass *c) {GSettingsBackendClass *b=G_SETTINGS_BACKEND_CLASS(c);b->read=locked_read;b->get_writable=locked_writable;}
static void locked_backend_init(LockedBackend *b) {}
void cc_luma_live_ready (CcLumaFixture *model, const char *adapter) {}
gboolean cc_luma_night_validate (CcLumaFixture *model) { return FALSE; }
gboolean cc_luma_pointer_validate (CcLumaFixture *model) { return FALSE; }
gboolean cc_luma_a11y_validate (CcLumaFixture *model) { return FALSE; }
static gboolean write_cb(CcLumaFixture *model, const char *key, JsonNode *value, gpointer data, GError **error)
{ return gsettings_write(data,key,value,error); }
static CcLumaFixture *model_new(void)
{
 g_autoptr(JsonParser) parser=json_parser_new();
 g_assert_true(json_parser_load_from_data(parser,"{\"settings\":{}}",-1,NULL));
 return cc_luma_fixture_new_live(json_parser_get_root(parser));
}
static JsonNode *sample(Bound *b)
{
 const CcLumaGsRow *r=b->row;
 g_autoptr(JsonNode) old=read_row(b);
 switch(r->kind) {
 case CC_LUMA_GS_BOOLEAN: case CC_LUMA_GS_NOT: case CC_LUMA_GS_UNLESS: case CC_LUMA_GS_ABOVE:
 return json_node_init_boolean(json_node_alloc(), !json_node_get_boolean(old));
 case CC_LUMA_GS_CHOICE: return json_node_init_string(json_node_alloc(), g_strcmp0(json_node_get_string(old),r->choices[0])==0 ? r->choices[2]:r->choices[0]);
 case CC_LUMA_GS_CHOICE_INT: return json_node_init_int(json_node_alloc(), 195);
 case CC_LUMA_GS_LINEAR: return json_node_init_int(json_node_alloc(), r->low+(r->high-r->low)/2);
 case CC_LUMA_GS_SCROLL: return json_node_init_int(json_node_alloc(),25);
 case CC_LUMA_GS_MINUTES: return json_node_init_string(json_node_alloc(),"5");
 case CC_LUMA_GS_HOURS: return json_node_init_string(json_node_alloc(),"21:45");
 case CC_LUMA_GS_DOUBLE: return json_node_init_double(json_node_alloc(),2.0);
 }
 g_assert_not_reached();
}
int main(void)
{
 g_setenv("GSETTINGS_BACKEND","memory",TRUE);
 g_autoptr(CcLumaFixture) model=model_new();
 CcLumaGsTable *t=gsettings_start(model);
 cc_luma_fixture_set_writer(model,write_cb,t);
 guint count=0;
 for(guint i=0;i<t->bound->len;i++) {
  Bound *b=g_ptr_array_index(t->bound,i);
  g_assert_true(cc_luma_fixture_key_verified(model,b->row->key));
  g_autoptr(JsonNode) desired=sample(b);
  g_autoptr(GError) error=NULL;
  g_assert_true(cc_luma_fixture_write(model,b->row->key,desired,&error));
  g_assert_no_error(error);
  g_autoptr(JsonNode) got=read_row(b);
  g_assert_true(json_node_equal(got,desired));
  CcLumaGsTable *again=gsettings_start(model);
  Bound *reopened=g_hash_table_lookup(again->keys,b->row->key);
  g_autoptr(JsonNode) got2=read_row(reopened);
  g_assert_true(json_node_equal(got2,desired));
  cc_luma_gs_table_free(again);
  g_autoptr(JsonNode) invalid=json_node_init_object(json_node_alloc(),json_object_new());
  g_assert_false(cc_luma_fixture_write(model,b->row->key,invalid,&error));
  g_assert_error(error,G_IO_ERROR,G_IO_ERROR_INVALID_ARGUMENT);
  g_print("PASS write/read/reopen/type-refusal %s\n",b->row->key); count++;
 }
 g_assert_cmpuint(count,>=,35);
 const char *missing[]={"absent-key",NULL};
 cc_luma_gs_table_verify(t,missing);
 g_assert_false(cc_luma_fixture_key_verified(model,"absent-key"));
 g_autoptr(JsonNode) out=json_node_init_double(json_node_alloc(),-1.0);
 g_autoptr(GError) error=NULL;
 g_assert_false(cc_luma_fixture_write(model,"zoomX",out,&error));
 g_assert_error(error,G_IO_ERROR,G_IO_ERROR_INVALID_ARGUMENT);
 Bound *locked=g_hash_table_lookup(t->keys,"dim");
 GSettings *previous=locked->settings;
 g_autoptr(GSettingsSchema) schema=g_settings_schema_source_lookup(g_settings_schema_source_get_default(),POWER,TRUE);
 g_autoptr(GSettingsBackend) backend=g_object_new(locked_backend_get_type(),NULL);
 g_autoptr(GSettings) policy=g_settings_new_full(schema,backend,NULL);
 locked->settings=policy;g_clear_error(&error);
 g_autoptr(JsonNode) toggle=json_node_init_boolean(json_node_alloc(),TRUE);
 g_assert_false(cc_luma_fixture_write(model,"dim",toggle,&error));
 g_assert_error(error,G_IO_ERROR,G_IO_ERROR_PERMISSION_DENIED);locked->settings=previous;
 g_print("PASS unsupported key, schema-range and backend-policy refusal; %u native rows\n",count);
 cc_luma_gs_table_free(t);
 return 0;
}
