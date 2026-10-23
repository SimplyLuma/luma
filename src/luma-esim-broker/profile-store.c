/* SPDX-License-Identifier: Apache-2.0 */
#include "profile-store.h"
#include <string.h>

static void wipe (void *data, gsize size) {
  volatile guint8 *p = data;
  while (size--) *p++ = 0;
}
static void profile_free (gpointer data) {
  LumaEsimProfile *p = data;
  if (p->selector) { wipe (p->selector, strlen (p->selector)); g_free (p->selector); }
  g_free (p->handle); g_free (p->name); g_free (p->nickname); g_free (p->provider); g_free (p);
}
void luma_esim_store_clear (LumaEsimStore *s) {
  g_clear_pointer (&s->generation, g_free);
  g_clear_pointer (&s->sender, g_free);
  g_clear_pointer (&s->profiles, g_ptr_array_unref);
}
static gboolean selector_valid (const char *s) {
  if (!s) return FALSE;
  gsize n = strlen (s);
  if (n != 19 && n != 20 && n != 32) return FALSE;
  for (gsize i=0; i<n; i++)
    if (!(n == 32 ? g_ascii_isxdigit (s[i]) : g_ascii_isdigit (s[i]))) return FALSE;
  return TRUE;
}
/* Carrier-controlled display fields are untrusted: bounded UTF-8, no terminal,
 * bidi override or control characters. A malformed field is a safe fallback,
 * never a format string, markup, path, or an identifier exposed to the UI. */
static char *label (JsonObject *o, const char *key, const char *fallback) {
  JsonNode *n = json_object_get_member (o, key);
  const char *s = n && JSON_NODE_HOLDS_VALUE (n) && json_node_get_value_type (n) == G_TYPE_STRING ? json_node_get_string (n) : NULL;
  if (!s || !*s || strlen (s)>256 || !g_utf8_validate (s, -1, NULL)) return g_strdup (fallback);
  for (const char *c=s; *c; c=g_utf8_next_char (c)) {
    gunichar x=g_utf8_get_char (c);
    if (g_unichar_iscntrl (x) || (x>=0x202a && x<=0x202e) || (x>=0x2066 && x<=0x2069)) return g_strdup (fallback);
  }
  return g_strdup (s);
}
static const char *string_member (JsonObject *o, const char *key) {
  JsonNode *n=json_object_get_member (o,key);
  return n && JSON_NODE_HOLDS_VALUE (n) && json_node_get_value_type (n)==G_TYPE_STRING ? json_node_get_string (n) : NULL;
}
/* Clear parser-owned copies, including ICCID/AID and unexpected nested data. */
static void wipe_json (JsonNode *n) {
  if (JSON_NODE_HOLDS_OBJECT (n)) {
    GList *members=json_object_get_members (json_node_get_object (n));
    for (GList *m=members;m;m=m->next) wipe_json (json_object_get_member (json_node_get_object (n),m->data));
    g_list_free (members);
  } else if (JSON_NODE_HOLDS_ARRAY (n)) {
    JsonArray *a=json_node_get_array (n);
    for (guint i=0;i<json_array_get_length(a);i++) wipe_json(json_array_get_element(a,i));
  } else if (JSON_NODE_HOLDS_VALUE (n) && json_node_get_value_type(n)==G_TYPE_STRING) {
    const char *s=json_node_get_string(n);
    if (s) wipe ((char *)s,strlen(s));
  }
}
static gint compare_sequence(gconstpointer a,gconstpointer b) {
  guint32 x=*(const guint32*)a,y=*(const guint32*)b;return (x>y)-(x<y);
}
/* ListNotification may arrive in any order. Deliver in ascending sequence order
 * so a later state notification cannot supersede an earlier installation result.
 * Retain only sequence numbers; wipe all parser-owned carrier metadata. */
GArray *luma_esim_notification_sequences(const guint8 *data,gsize size) {
  g_autoptr(JsonParser) parser=json_parser_new();
  GArray *sequences=g_array_new(FALSE,FALSE,sizeof(guint32));
  gboolean ok=FALSE;
  if(!data || !size || size>1024*1024 || memchr(data,0,size) ||
     !json_parser_load_from_data(parser,(const char*)data,size,NULL))goto out;
  JsonNode *root=json_parser_get_root(parser);
  if(!JSON_NODE_HOLDS_OBJECT(root))goto out;
  JsonNode *payload=json_object_get_member(json_node_get_object(root),"payload");
  if(!payload || !JSON_NODE_HOLDS_OBJECT(payload))goto out;
  JsonObject *object=json_node_get_object(payload);
  JsonNode *code=json_object_get_member(object,"code"),*rows=json_object_get_member(object,"data");
  if(!code || !JSON_NODE_HOLDS_VALUE(code) || json_node_get_value_type(code)!=G_TYPE_INT64 ||
     json_node_get_int(code)!=0 || !rows || !JSON_NODE_HOLDS_ARRAY(rows))goto out;
  JsonArray *array=json_node_get_array(rows);
  if(json_array_get_length(array)>256)goto out;
  for(guint i=0;i<json_array_get_length(array);i++){
    JsonNode *row=json_array_get_element(array,i);
    if(!JSON_NODE_HOLDS_OBJECT(row))goto out;
    JsonNode *seq=json_object_get_member(json_node_get_object(row),"seqNumber");
    if(!seq || !JSON_NODE_HOLDS_VALUE(seq) || json_node_get_value_type(seq)!=G_TYPE_INT64)goto out;
    gint64 value=json_node_get_int(seq);if(value<0 || value>G_MAXUINT32)goto out;
    guint32 n=(guint32)value;g_array_append_val(sequences,n);
  }
  g_array_sort(sequences,compare_sequence);
  for(guint i=1;i<sequences->len;i++)
    if(g_array_index(sequences,guint32,i)==g_array_index(sequences,guint32,i-1))goto out;
  ok=TRUE;
 out:
  if(json_parser_get_root(parser))wipe_json(json_parser_get_root(parser));
  if(!ok)g_clear_pointer(&sequences,g_array_unref);
  return sequences;
}
gboolean luma_esim_store_load (LumaEsimStore *s, const char *sender,
                               const guint8 *data, gsize size, GError **error) {
  gboolean ok=FALSE;
  LumaEsimStore next={0};
  g_autoptr(JsonParser) parser=json_parser_new();
  if (!sender || !*sender || size==0 || size>1024*1024 || memchr(data,0,size) ||
      !json_parser_load_from_data(parser,(const char *)data,size,NULL)) goto out;
  JsonNode *root=json_parser_get_root(parser);
  if (!JSON_NODE_HOLDS_OBJECT(root)) goto out;
  JsonObject *envelope=json_node_get_object(root);
  JsonNode *payload_node=json_object_get_member(envelope,"payload");
  if (!payload_node || !JSON_NODE_HOLDS_OBJECT(payload_node)) goto out;
  JsonObject *payload=json_node_get_object(payload_node);
  JsonNode *code=json_object_get_member(payload,"code");
  JsonNode *rows=json_object_get_member(payload,"data");
  if (!code || !JSON_NODE_HOLDS_VALUE(code) || json_node_get_value_type(code)!=G_TYPE_INT64 ||
      json_node_get_int(code)!=0 || !rows || !JSON_NODE_HOLDS_ARRAY(rows)) goto out;
  JsonArray *a=json_node_get_array(rows);
  if (json_array_get_length(a)>128) goto out;
  next.generation=g_uuid_string_random();next.sender=g_strdup(sender);
  next.profiles=g_ptr_array_new_with_free_func(profile_free);
  for(guint i=0;i<json_array_get_length(a);i++) {
    JsonNode *row=json_array_get_element(a,i);
    if(!JSON_NODE_HOLDS_OBJECT(row))goto out;
    JsonObject *o=json_node_get_object(row);
    const char *id=string_member(o,"iccid"), *state=string_member(o,"profileState");
    if(!selector_valid(id) || !state || (strcmp(state,"enabled") && strcmp(state,"disabled")))goto out;
    for(guint j=0;j<next.profiles->len;j++)
      if(g_str_equal(((LumaEsimProfile*)g_ptr_array_index(next.profiles,j))->selector,id))goto out;
    LumaEsimProfile *p=g_new0(LumaEsimProfile,1);
    p->selector=g_strdup(id);p->handle=g_uuid_string_random();p->enabled=g_str_equal(state,"enabled");
    p->nickname=label(o,"profileNickname","");p->name=g_strdup(p->nickname);
    if(!*p->name){g_free(p->name);p->name=label(o,"profileName","Mobile plan");}
    p->provider=label(o,"serviceProviderName","");
    g_ptr_array_add(next.profiles,p);
  }
  luma_esim_store_clear(s);*s=next;memset(&next,0,sizeof next);ok=TRUE;
out:
  if(json_parser_get_root(parser))wipe_json(json_parser_get_root(parser));
  luma_esim_store_clear(&next);
  if(!ok) {
    luma_esim_store_clear(s);
    g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_INVALID_DATA,"The eSIM profile inventory could not be verified");
  }
  return ok;
}
GVariant *luma_esim_store_public (const LumaEsimStore *s) {
  GVariantBuilder rows;g_variant_builder_init(&rows,G_VARIANT_TYPE("aa{sv}"));
  for(guint i=0;s->profiles && i<s->profiles->len;i++) {
    LumaEsimProfile *p=g_ptr_array_index(s->profiles,i);
    GVariantBuilder row;g_variant_builder_init(&row,G_VARIANT_TYPE("a{sv}"));
    g_variant_builder_add(&row,"{sv}","id",g_variant_new_string(p->handle));
    g_variant_builder_add(&row,"{sv}","name",g_variant_new_string(p->name));
    g_variant_builder_add(&row,"{sv}","provider",g_variant_new_string(p->provider));
    /* Display hint only: suffixes can collide. Mutations still require the
     * sender-bound generation and opaque handle, never this partial ICCID. */
    g_variant_builder_add(&row,"{sv}","identifier-suffix",
      g_variant_new_string(p->selector+strlen(p->selector)-4));
    g_variant_builder_add(&row,"{sv}","enabled",g_variant_new_boolean(p->enabled));
    g_variant_builder_add_value(&rows,g_variant_builder_end(&row));
  }
  return g_variant_new("(s@aa{sv})",s->generation?s->generation:"",g_variant_builder_end(&rows));
}
const LumaEsimProfile *luma_esim_store_select (const LumaEsimStore *s,const char *sender,const char *generation,const char *handle) {
  if(!s->profiles || !s->sender || !s->generation || !g_str_equal(s->sender,sender) || !g_str_equal(s->generation,generation))return NULL;
  for(guint i=0;i<s->profiles->len;i++) {
    LumaEsimProfile *p=g_ptr_array_index(s->profiles,i);
    if(g_str_equal(p->handle,handle))return p;
  }
  return NULL;
}
gboolean luma_esim_store_equal (const LumaEsimStore *a,const LumaEsimStore *b) {
  if(!a->profiles || !b->profiles || a->profiles->len!=b->profiles->len)return FALSE;
  for(guint i=0;i<a->profiles->len;i++) {
    LumaEsimProfile *x=g_ptr_array_index(a->profiles,i), *found=NULL;
    for(guint j=0;j<b->profiles->len;j++) {
      LumaEsimProfile *y=g_ptr_array_index(b->profiles,j);
      if(g_str_equal(x->selector,y->selector)){found=y;break;}
    }
    if(!found || x->enabled!=found->enabled || !g_str_equal(x->name,found->name) || !g_str_equal(x->nickname,found->nickname) || !g_str_equal(x->provider,found->provider))return FALSE;
  }
  return TRUE;
}
