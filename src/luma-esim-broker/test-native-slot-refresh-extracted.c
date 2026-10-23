/* SPDX-License-Identifier: GPL-2.0-or-later
 * Verbatim production slot functions; controlled QMI outputs/owner and constructor surfaces.
 * These controls do not establish modem discovery or generated API compilation. */
#include <glib-object.h>
#include <string.h>
#include <stdarg.h>
#define EID_BYTE_LENGTH 16
static const gchar bcd_chars[]="0123456789ABCDEF";
typedef struct {guint physical_slot_status,physical_card_status,logical_slot;GArray *iccid;} QmiPhysicalSlotStatusSlot;
typedef struct {gboolean is_euicc;} QmiPhysicalSlotInformationSlot;
typedef struct {GArray *eid;} QmiSlotEidElement;
typedef struct {GArray *slots_status;} Private;
typedef struct {gchar *eid;gboolean active;guint type,status,invalidations;} MMBaseSim;
typedef struct {Private priv;GPtrArray *sims;guint reprobes,updates;} MMSharedQmi;
typedef struct {GArray *status,*information,*eids;} QmiIndicationUimSlotStatusOutput;
typedef gpointer QmiClientUim;
#define QMI_UIM_PHYSICAL_CARD_STATE_PRESENT 1
#define QMI_UIM_SLOT_STATE_ACTIVE 1
#define MM_IFACE_MODEM_SIM_SLOTS "slots"
#define MM_IFACE_MODEM(x) (x)
#define MM_BASE_MODEM(x) (x)
#define MM_GDBUS_SIM(x) (x)
#define mm_obj_dbg(...) ((void)0)
#define mm_obj_warn(...) ((void)0)
static void free_sim(MMBaseSim *sim){if(sim){g_free(sim->eid);g_free(sim);}}
G_DEFINE_AUTOPTR_CLEANUP_FUNC(MMBaseSim,free_sim)
static void fixture_get(MMSharedQmi *self,const char *name,GPtrArray **slots,gpointer end){(void)name;(void)end;*slots=g_ptr_array_ref(self->sims);}
#define g_object_get fixture_get
static Private *get_private(MMSharedQmi *self){return &self->priv;}
static const gchar *mm_gdbus_sim_get_eid(MMBaseSim *sim){return sim->eid;}
static void mm_sim_qmi_invalidate_native_operation(MMBaseSim *sim){sim->invalidations++;}
static void mm_iface_modem_process_sim_event(MMSharedQmi *self){self->reprobes++;}
static void mm_iface_modem_modify_sim(MMSharedQmi *self,guint index,MMBaseSim *sim){MMBaseSim *old=g_ptr_array_index(self->sims,index);g_assert_true(!old||old->invalidations>0);free_sim(old);MMBaseSim *next=sim?g_memdup2(sim,sizeof *sim):NULL;if(next)next->eid=g_strdup(sim->eid);g_ptr_array_index(self->sims,index)=next;self->updates++;}
static gchar *mm_utils_bin2hexstr(const guint8 *data,gsize len){GString *s=g_string_new(NULL);for(gsize i=0;i<len;i++)g_string_append_printf(s,"%02X",data[i]);return g_string_free(s,FALSE);}
static gchar *mm_3gpp_parse_iccid(const gchar *raw,GError **error){(void)error;return g_strdup(raw);}
static gboolean luma_native_eid_valid(const gchar *eid);
typedef MMSharedQmi MMBaseModem;
#define MM_TYPE_SIM_QMI 1
#define MM_BASE_SIM(x) (x)
#undef G_OBJECT
#define G_OBJECT(x) (x)
#define MM_BASE_SIM_MODEM "modem"
#define MM_BIND_TO "bind-to"
#define MM_SIM_QMI_DMS_UIM_DEPRECATED "deprecated"
#define MM_BASE_SIM_SLOT_NUMBER "slot-number"
#define MM_SIM_TYPE_ESIM 2
#define MM_SIM_ESIM_STATUS_WITH_PROFILES 1
static gpointer fixture_new(guint type,...){
 (void)type;MMBaseSim *s=g_new0(MMBaseSim,1);va_list ap;va_start(ap,type);const char *name;
 while((name=va_arg(ap,const char *))){
  if(!strcmp(name,"active"))s->active=va_arg(ap,int);
  else if(!strcmp(name,"deprecated")||!strcmp(name,"slot-number"))(void)va_arg(ap,int);
  else {gpointer value=va_arg(ap,gpointer);if(!strcmp(name,"eid"))s->eid=g_strdup(value);}
 }
 va_end(ap);return s;
}
#undef G_OBJECT
#define G_OBJECT(x) (x)
#define g_object_new fixture_new
static void mm_gdbus_sim_set_sim_type(MMBaseSim *sim,guint type){sim->type=type;}
static void mm_gdbus_sim_set_esim_status(MMBaseSim *sim,guint status){sim->status=status;}
static void mm_base_sim_export(MMBaseSim *sim){(void)sim;}
MMBaseSim *mm_sim_qmi_new_initialized(MMBaseModem *,gboolean,guint,gboolean,const gchar *,const gchar *,const gchar *,const gchar *,const gchar *,const GStrv);

static gboolean qmi_indication_uim_slot_status_output_get_physical_slot_status(QmiIndicationUimSlotStatusOutput *o,GArray **a,GError **error){(void)error;*a=o->status;return *a!=NULL;}
static gboolean qmi_indication_uim_slot_status_output_get_physical_slot_information(QmiIndicationUimSlotStatusOutput *o,GArray **a,GError **error){(void)error;*a=o->information;return *a!=NULL;}
static gboolean qmi_indication_uim_slot_status_output_get_slot_eid(QmiIndicationUimSlotStatusOutput *o,GArray **a,GError **error){(void)error;*a=o->eids;return *a!=NULL;}
gchar *
mm_bcd_to_string (const guint8 *bcd, gsize bcd_len, gboolean low_nybble_first)
{
    GString *str;
    gsize i;

    g_return_val_if_fail (bcd != NULL, NULL);

    str = g_string_sized_new (bcd_len * 2 + 1);
    for (i = 0 ; i < bcd_len; i++) {
        if (low_nybble_first)
            str = g_string_append_c (str, bcd_chars[bcd[i] & 0xF]);
        str = g_string_append_c (str, bcd_chars[(bcd[i] >> 4) & 0xF]);
        if (!low_nybble_first)
            str = g_string_append_c (str, bcd_chars[bcd[i] & 0xF]);
    }
    return g_string_free (str, FALSE);
}

gchar *
mm_decode_eid (const gchar *eid, gsize eid_len)
{
    if (eid_len != EID_BYTE_LENGTH)
        return NULL;

    return mm_bcd_to_string ((const guint8 *) eid, eid_len, FALSE /* low_nybble_first */);
}

static gboolean
luma_native_eid_valid (const gchar *eid)
{
    guint i;
    if (!eid || strlen (eid) != 32)
        return FALSE;
    for (i = 0; i < 32; i++) {
        if (!g_ascii_isdigit (eid[i]))
            return FALSE;
    }
    return TRUE;
}

MMBaseSim *
mm_sim_qmi_new_initialized (MMBaseModem *modem,
                            gboolean     dms_uim_deprecated,
                            guint        slot_number,
                            gboolean     active,
                            const gchar *sim_identifier,
                            const gchar *imsi,
                            const gchar *eid,
                            const gchar *operator_identifier,
                            const gchar *operator_name,
                            const GStrv  emergency_numbers)
{
    MMBaseSim *sim;

    sim = MM_BASE_SIM (g_object_new (MM_TYPE_SIM_QMI,
                                     MM_BASE_SIM_MODEM,             modem,
                                     MM_BIND_TO,                    G_OBJECT (modem),
                                     MM_SIM_QMI_DMS_UIM_DEPRECATED, dms_uim_deprecated,
                                     MM_BASE_SIM_SLOT_NUMBER,       slot_number,
                                     "active",                      active,
                                     "sim-identifier",              sim_identifier,
                                     "imsi",                        imsi,
                                     "eid",                         eid,
                                     "operator-identifier",         operator_identifier,
                                     "operator-name",               operator_name,
                                     "emergency-numbers",           emergency_numbers,
                                     NULL));

    /* Native slot-status construction already decoded this slot's eUICC EID.
     * Do not infer an embedded card from ICCID/profile presence. */
    if (luma_native_eid_valid (eid)) {
        mm_gdbus_sim_set_sim_type (MM_GDBUS_SIM (sim), MM_SIM_TYPE_ESIM);
        /* ICCID presence proves a profile; absence does not prove the card's
         * inventory is empty (all profiles may simply be disabled). */
        if (sim_identifier && *sim_identifier)
            mm_gdbus_sim_set_esim_status (MM_GDBUS_SIM (sim), MM_SIM_ESIM_STATUS_WITH_PROFILES);
    }
    mm_base_sim_export (sim);
    return sim;
}

static gchar *
slot_native_eid (GArray *information, GArray *eids, guint index)
{
    QmiPhysicalSlotInformationSlot *info;
    QmiSlotEidElement *element;
    gchar *eid;
    guint i;

    if (!information || !eids || index >= information->len || index >= eids->len)
        return NULL;
    info = &g_array_index (information, QmiPhysicalSlotInformationSlot, index);
    element = &g_array_index (eids, QmiSlotEidElement, index);
    if (!info->is_euicc || !element->eid || element->eid->len != 16)
        return NULL;
    eid = mm_decode_eid (element->eid->data, element->eid->len);
    if (!eid || strlen (eid) != 32) {
        g_free (eid);
        return NULL;
    }
    for (i = 0; i < 32; i++) {
        if (!g_ascii_isdigit (eid[i])) {
            g_free (eid);
            return NULL;
        }
    }
    return eid;
}

static void
invalidate_native_slot (MMSharedQmi *self, guint index)
{
    GPtrArray *slots = NULL;

    g_object_get (self, MM_IFACE_MODEM_SIM_SLOTS, &slots, NULL);
    if (slots && index < slots->len && g_ptr_array_index (slots, index))
        mm_sim_qmi_invalidate_native_operation (g_ptr_array_index (slots, index));
    if (slots)
        g_ptr_array_unref (slots);
}

static void
update_sim_from_slot_status (MMSharedQmi               *self,
                             QmiPhysicalSlotStatusSlot *slot_status,
                             guint                      slot_index,
                             const gchar               *eid)
{
    g_autoptr(MMBaseSim)  sim = NULL;
    g_autofree gchar     *raw_iccid = NULL;
    g_autofree gchar     *iccid = NULL;
    g_autoptr(GError)     inner_error = NULL;

    mm_obj_dbg (self, "Updating sim at slot %d", slot_index + 1);
    /* If sim is not present, ask mm_iface_modem to clear the sim object */
    if (slot_status->physical_card_status != QMI_UIM_PHYSICAL_CARD_STATE_PRESENT && !eid) {
        mm_iface_modem_modify_sim (MM_IFACE_MODEM (self), slot_index, NULL);
        return;
    }

    if (!slot_status->iccid->len && !eid) {
        mm_iface_modem_modify_sim (MM_IFACE_MODEM (self), slot_index, NULL);
        return;
    }
    if (slot_status->iccid->len) {
        raw_iccid = mm_utils_bin2hexstr ((const guint8 *)slot_status->iccid->data, slot_status->iccid->len);
        if (!raw_iccid) {
            mm_obj_warn (self, "not creating SIM object: failed to convert ICCID from BCD");
            return;
        }
        iccid = mm_3gpp_parse_iccid (raw_iccid, &inner_error);
        if (!iccid) {
            mm_obj_warn (self, "not creating SIM object: couldn't parse SIM iccid: %s", inner_error->message);
            return;
        }
    }
    sim = mm_sim_qmi_new_initialized (MM_BASE_MODEM (self),
                                      TRUE,                              /* consider DMS UIM deprecated if we're creating SIM slots */
                                      slot_index + 1,                    /* slot number is the array index starting at 1 */
                                      slot_status->physical_slot_status, /* is_active */
                                      iccid,
                                      NULL,  /* imsi unknown */
                                      eid,   /* actual native slot EID */
                                      NULL,  /* operator id unknown */
                                      NULL,  /* operator name unknown */
                                      NULL); /* emergency numbers unknown */

    mm_iface_modem_modify_sim (MM_IFACE_MODEM (self), slot_index, sim);
}

static gboolean
slot_status_equal (QmiPhysicalSlotStatusSlot *slot_a,
                   QmiPhysicalSlotStatusSlot *slot_b)
{
    guint j;

    if (slot_a->physical_slot_status != slot_b->physical_slot_status)
        return FALSE;
    if (slot_a->physical_card_status != slot_b->physical_card_status)
        return FALSE;
    if (slot_a->iccid->len != slot_b->iccid->len)
        return FALSE;
    for (j = 0; j < slot_a->iccid->len; j++) {
        if (g_array_index (slot_a->iccid, guint8, j) != g_array_index (slot_b->iccid, guint8, j))
            return FALSE;
    }
    return TRUE;
}

static gboolean
slot_array_status_equal (GArray   *slots_status1,
                         GArray   *slots_status2,
                         gboolean  check_active_slots_only)
{
    guint i;

    if (!slots_status1 && !slots_status2)
        return TRUE;
    if (!slots_status1 || !slots_status2 || slots_status1->len != slots_status2->len)
        return FALSE;
    for (i = 0; i < slots_status1->len; i++) {
        /* Compare slot at index i from slots_status1 and slots_status2 */
        QmiPhysicalSlotStatusSlot *slot_a;
        QmiPhysicalSlotStatusSlot *slot_b;

        slot_a = &g_array_index (slots_status1, QmiPhysicalSlotStatusSlot, i);
        slot_b = &g_array_index (slots_status2, QmiPhysicalSlotStatusSlot, i);

        /* Check that slot_a and slot_b have the same slot status (i.e. active or inactive) */
        if (slot_a->physical_slot_status != slot_b->physical_slot_status)
            return FALSE;
        /* Once slot_a and slot_b are confirmed to have the same physical slot status,
         * we will ignore inactive slots if check_active_slots_only is set. */
        if (check_active_slots_only && slot_a->physical_slot_status != QMI_UIM_SLOT_STATE_ACTIVE) {
            g_assert (slot_a->physical_slot_status == slot_b->physical_slot_status);
            continue;
        }
        if (!slot_status_equal (slot_a, slot_b))
            return FALSE;
    }
    return TRUE;
}

static void
uim_slot_status_indication_cb (QmiClientUim                     *client,
                               QmiIndicationUimSlotStatusOutput *output,
                               MMSharedQmi                      *self)
{
    GArray            *new_slots_status = NULL;
    GArray            *information = NULL;
    GArray            *eids = NULL;
    GPtrArray         *sim_slots = NULL;
    Private           *priv;
    guint              i;
    g_autoptr(GError)  error = NULL;

    (void) client;
    priv = get_private (self);
    mm_obj_dbg (self, "received slot status indication");

    if (!priv->slots_status) {
        mm_obj_dbg (self, "initial slot status is not loaded yet");
        return;
    }

    if (!qmi_indication_uim_slot_status_output_get_physical_slot_status (output,
                                                                         &new_slots_status,
                                                                         &error)) {
        mm_obj_warn (self, "could not process slot status indication: %s", error->message);
        return;
    }

    qmi_indication_uim_slot_status_output_get_physical_slot_information (output, &information, NULL);
    qmi_indication_uim_slot_status_output_get_slot_eid (output, &eids, NULL);
    if ((information && information->len != new_slots_status->len) ||
        (eids && eids->len != new_slots_status->len)) {
        mm_obj_warn (self, "malformed native slot indication metadata");
        return;
    }

    /* A slot status indication means that
     * 1) The physical slot to logical slot mapping has changed as a
     * result of switching the slot. or,
     * 2) A card has been removed from, or inserted to, the physical slot. or,
     * 3) A physical slot is powered up or down. */

    /* Reprobe if the active slot changed or the information in an
     * active slot's status changed */
    if (!slot_array_status_equal (priv->slots_status, new_slots_status, TRUE)) {
        mm_obj_dbg (self, "An active slot had a status change, will reprobe the modem");
        for (i = 0; i < priv->slots_status->len; i++)
            invalidate_native_slot (self, i);
        mm_iface_modem_process_sim_event (MM_IFACE_MODEM (self));
        return;
    }

    /* An inactive slot changed, we won't be reprobing the modem.
     * Instead, we will ask mm_iface_modem to update the sim object.
     * Iterate over each slot to identify the slots that changed state.*/
    g_object_get (self, MM_IFACE_MODEM_SIM_SLOTS, &sim_slots, NULL);
    for (i = 0; i < priv->slots_status->len; i++) {
        g_autofree gchar *eid = slot_native_eid (information, eids, i);
        MMBaseSim *old_sim = sim_slots && i < sim_slots->len ? g_ptr_array_index (sim_slots, i) : NULL;
        const gchar *old_eid = old_sim ? mm_gdbus_sim_get_eid (MM_GDBUS_SIM (old_sim)) : NULL;
        gboolean eid_changed = information && eids && g_strcmp0 (old_eid, eid);
        QmiPhysicalSlotStatusSlot *old_slot;
        QmiPhysicalSlotStatusSlot *new_slot;

        old_slot = &g_array_index (priv->slots_status, QmiPhysicalSlotStatusSlot, i);
        new_slot = &g_array_index (new_slots_status, QmiPhysicalSlotStatusSlot, i);

        if ((eid_changed && new_slot->physical_slot_status == QMI_UIM_SLOT_STATE_ACTIVE) ||
            ((!information || !eids) && old_eid && !slot_status_equal (old_slot, new_slot))) {
            guint j;
            for (j = 0; j < priv->slots_status->len; j++)
                invalidate_native_slot (self, j);
            g_clear_pointer (&sim_slots, g_ptr_array_unref);
            mm_iface_modem_process_sim_event (MM_IFACE_MODEM (self));
            return;
        }
        if (!slot_status_equal (old_slot, new_slot) || eid_changed) {
            invalidate_native_slot (self, i);
            mm_obj_dbg (self, "Slot %d (inactive) had a status change. Will update sims, but not reprobe", i + 1);
            update_sim_from_slot_status (self, new_slot, i, eid);
        }
    }

    g_clear_pointer (&sim_slots, g_ptr_array_unref);
    g_clear_pointer (&priv->slots_status, g_array_unref);
    priv->slots_status = g_array_ref (new_slots_status);
}

static GArray *bytes(guint len,guint8 value){GArray *a=g_array_new(FALSE,TRUE,1);g_array_set_size(a,len);memset(a->data,value,len);return a;}
int main(void){
 MMSharedQmi self={0};self.sims=g_ptr_array_new();g_ptr_array_add(self.sims,NULL);
 QmiPhysicalSlotStatusSlot slot={0,1,0,NULL};slot.iccid=bytes(0,0);
 QmiPhysicalSlotInformationSlot info={TRUE};QmiSlotEidElement e={bytes(16,0x12)};
 QmiIndicationUimSlotStatusOutput out={g_array_new(FALSE,FALSE,sizeof slot),g_array_new(FALSE,FALSE,sizeof info),g_array_new(FALSE,FALSE,sizeof e)};
 g_array_append_val(out.status,slot);g_array_append_val(out.information,info);g_array_append_val(out.eids,e);self.priv.slots_status=g_array_ref(out.status);
 /* EID absent->present, unchanged empty ICCID: constructs native inactive object. */
 uim_slot_status_indication_cb(NULL,&out,&self);g_assert_cmpuint(self.updates,==,1);MMBaseSim *s=g_ptr_array_index(self.sims,0);g_assert_cmpuint(s->type,==,2);g_assert_false(s->active);g_assert_cmpuint(s->status,==,0);
 /* Same identity emits no replacement; A->B->A replaces with fresh observed EID. */
 uim_slot_status_indication_cb(NULL,&out,&self);g_assert_cmpuint(self.updates,==,1);
 memset(e.eid->data,0x34,16);uim_slot_status_indication_cb(NULL,&out,&self);g_assert_cmpuint(self.updates,==,2);
 memset(e.eid->data,0x12,16);uim_slot_status_indication_cb(NULL,&out,&self);g_assert_cmpuint(self.updates,==,3);
 /* Malformed EID rejects native type, including nondecimal BCD. */
 memset(e.eid->data,0xAF,16);g_assert_null(slot_native_eid(out.information,out.eids,0));memset(e.eid->data,0x12,16);
 g_array_set_size(out.information,0);uim_slot_status_indication_cb(NULL,&out,&self);g_assert_cmpuint(self.updates,==,3);g_array_append_val(out.information,info);
 /* EID-only active-slot change invalidates before authoritative reprobe. */
 g_array_index(out.status,QmiPhysicalSlotStatusSlot,0).physical_slot_status=1;
 g_array_unref(self.priv.slots_status);self.priv.slots_status=g_array_ref(out.status);
 memset(e.eid->data,0x56,16);uim_slot_status_indication_cb(NULL,&out,&self);g_assert_cmpuint(self.reprobes,==,1);s=g_ptr_array_index(self.sims,0);g_assert_cmpuint(s->invalidations,==,1);
 /* Changed native physical status with missing EID metadata reprobes, never guesses prior EID. */
 out.information=NULL;out.status=g_array_new(FALSE,FALSE,sizeof slot);slot.physical_slot_status=0;g_array_append_val(out.status,slot);uim_slot_status_indication_cb(NULL,&out,&self);g_assert_cmpuint(self.reprobes,==,2);

 /* A blank eUICC may report profile-card absent but provide its native EID. */
 MMSharedQmi blank={0};blank.sims=g_ptr_array_new();MMBaseSim *old=g_new0(MMBaseSim,1);old->eid=g_strdup("12121212121212121212121212121212");old->type=2;g_ptr_array_add(blank.sims,old);
 QmiPhysicalSlotStatusSlot before={0,1,0,bytes(0,0)},after={0,0,0,before.iccid};
 blank.priv.slots_status=g_array_new(FALSE,FALSE,sizeof before);g_array_append_val(blank.priv.slots_status,before);
 QmiIndicationUimSlotStatusOutput absent={g_array_new(FALSE,FALSE,sizeof after),g_array_new(FALSE,FALSE,sizeof info),g_array_new(FALSE,FALSE,sizeof e)};g_array_append_val(absent.status,after);g_array_append_val(absent.information,info);memset(e.eid->data,0x12,16);g_array_append_val(absent.eids,e);
 uim_slot_status_indication_cb(NULL,&absent,&blank);g_assert_cmpuint(blank.updates,==,1);old=g_ptr_array_index(blank.sims,0);g_assert_nonnull(old);g_assert_cmpuint(old->type,==,2);g_assert_cmpuint(old->status,==,0);
 /* Invalid actual native EID cannot retain the physically absent object. */
 memset(e.eid->data,0xAF,16);uim_slot_status_indication_cb(NULL,&absent,&blank);g_assert_cmpuint(blank.updates,==,2);g_assert_null(g_ptr_array_index(blank.sims,0));
 /* A removed physical SIM with no EID is cleared normally. */
 old=g_new0(MMBaseSim,1);old->type=1;g_ptr_array_index(blank.sims,0)=old;g_array_unref(blank.priv.slots_status);blank.priv.slots_status=g_array_new(FALSE,FALSE,sizeof before);g_array_append_val(blank.priv.slots_status,before);absent.information=NULL;absent.eids=NULL;
 uim_slot_status_indication_cb(NULL,&absent,&blank);g_assert_cmpuint(blank.updates,==,3);g_assert_null(g_ptr_array_index(blank.sims,0));
 return 0;
}
