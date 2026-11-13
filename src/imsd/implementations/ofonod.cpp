// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types no-char-pointer
/*
imsd-ofonod — GNOME Calls backend for imsd (Phosh, GNOME Mobile).

Presents imsd as an oFono modem on the system bus: `org.ofono` with a Manager
at `/`, one Modem + VoiceCallManager at `/imsd`, and one VoiceCall object per
live call. GNOME Calls' bundled `ofono` provider plugin then drives imsd —
dial, ring, answer, hang up, call history, contact lookup — with Calls, Phosh
and GNOME Shell unmodified. It is the exact mirror of what imsd-dialerd does
for Plasma Mobile's org.kde.telephony.*.

Why this seam and not org.gnome.Calls: that interface is what Calls *exports*
so a shell can observe and control a call that already exists (Accept, Hangup,
SendDtmf, Silence — there is no Dial, and no way to register as a call
source). Phosh consumes it from Calls; nobody provides it to Calls. Calls'
own backends are libpeas provider plugins (mm, ofono, sip, dummy) linked
against private headers that no distro installs, so `org.ofono` is the only
D-Bus seam a third party can implement.

Consumer-shaped details, verified against calls 50.0
plugins/provider/ofono/ — get these wrong and the plugin silently ignores us:
  - the provider watches `org.ofono` on the SYSTEM bus and proxies `/` as
    org.ofono.Manager. A modem is an Origin exactly while its `Interfaces`
    property lists "org.ofono.VoiceCallManager" — and Calls 50.0 never
    disconnects a removed Origin's CallAdded handler, so every withdraw/
    re-add cycle leaves a stale handler behind (calls announced twice, then
    a crash on freed memory; measured on the FP6). The interface is
    therefore listed PERMANENTLY; registration is reported through the
    modem's `Online` property and enforced in Dial, which fails legibly
    while imsd is down or unregistered. Only this daemon's own restart
    (org.ofono vanishing) still cycles the Origin.
  - VoiceCall `State` strings are parsed as CallsCallState enum nicks:
    dialing / alerting / incoming / active / held / disconnected. imsd's
    "ringing" (a 180 from the far end) is oFono's "alerting".
  - inbound-ness is derived ONCE, from State == "incoming" as the call is
    added, so an MT call must be announced incoming and never corrected later.
  - DisconnectReason is cached by the plugin and read when CallRemoved
    arrives — emit it before the removal or the history entry loses its
    reason.
  - Dial's returned object path is ignored (the plugin waits for CallAdded).
    We return a real one anyway.

Runs as root because `org.ofono` is a system-bus name (policy in
packaging/org.ofono.conf). It does no I/O beyond D-Bus: every call decision
stays in imsd. Its calls reach imsd as root, so it asks polkit about its own
callers (ImsdBus): call control is net.catcrafts.IMS1.Voice, as on imsd.
Everything happens on the GLib main loop, so there is no locking.

  --session   own org.ofono on the session bus and talk to imsd there
              (development: no root, no policy file, no polkit)
  IMSD_BUS=session|system   override only the imsd side of that choice
*/

#include <gio/gio.h>
#include <glib-unix.h>

import std;
import ImsdBus;

namespace {

constexpr const char* ImsdBus = "net.catcrafts.IMS1";
constexpr const char* ImsdPath = "/net/catcrafts/IMS1";
constexpr const char* ImsdIface = "net.catcrafts.IMS1";

constexpr const char* OfonoBus = "org.ofono";
constexpr const char* ManagerPath = "/";
constexpr const char* ManagerIface = "org.ofono.Manager";
constexpr const char* ModemPath = "/imsd";
constexpr const char* ModemIface = "org.ofono.Modem";
constexpr const char* VcmIface = "org.ofono.VoiceCallManager";
constexpr const char* CallIface = "org.ofono.VoiceCall";
constexpr const char* SsIface = "org.ofono.SupplementaryServices";

constexpr const char* ErrFailed = "org.ofono.Error.Failed";
constexpr const char* ErrNotImplemented = "org.ofono.Error.NotImplemented";
constexpr const char* ErrNotSupported = "org.ofono.Error.NotSupported";
constexpr const char* ErrInvalidArgs = "org.ofono.Error.InvalidArguments";

void Log(std::string_view m) { std::println("imsd-ofonod: {}", m); std::fflush(stdout); }

std::string EnvOr(const char* k, std::string d) {
    const char* v = std::getenv(k);
    return v ? std::string(v) : std::move(d);
}

// ---- state (main thread only) ----------------------------------------------
GDBusConnection* Bus = nullptr;       // where we own org.ofono
GDBusConnection* Imsd = nullptr;      // where net.catcrafts.IMS1 lives
GMainLoop* Loop = nullptr;
bool ImsdUp = false;
bool Registered = false;
std::vector<std::string> EmergencyNumbers;

struct CallRec {
    std::string uni, path, number;
    std::string state = "dialing";     // oFono nick, already mapped
    std::string reason;                // oFono DisconnectReason, once known
    bool inbound = false;
    std::int64_t startedAt = 0;
    guint regId = 0;                   // exported VoiceCall object
};

std::map<std::string, CallRec> Calls;         // uni -> record
std::map<std::string, std::string> ByPath;    // object path -> uni
std::map<std::string, std::string> UniToPath; // uni -> object path (survives
                                              // a Dial reply that races the
                                              // CallAdded signal)
unsigned PathSeq = 0;

// ---- GVariant helpers -------------------------------------------------------
std::string DictStr(GVariant* dict, const char* key) {
    GVariant* v = g_variant_lookup_value(dict, key, G_VARIANT_TYPE_STRING);
    if (!v) return "";
    std::string s = g_variant_get_string(v, nullptr);
    g_variant_unref(v);
    return s;
}
std::int64_t DictInt64(GVariant* dict, const char* key) {
    GVariant* v = g_variant_lookup_value(dict, key, G_VARIANT_TYPE_INT64);
    if (!v) return 0;
    std::int64_t x = g_variant_get_int64(v);
    g_variant_unref(v);
    return x;
}
bool DictBool(GVariant* dict, const char* key) {
    GVariant* v = g_variant_lookup_value(dict, key, G_VARIANT_TYPE_BOOLEAN);
    if (!v) return false;
    bool b = g_variant_get_boolean(v);
    g_variant_unref(v);
    return b;
}

// ---- imsd <-> oFono vocabulary ---------------------------------------------
// imsd call states -> oFono VoiceCall State nicks. Returns nullptr for a state
// we don't know, so the caller keeps the one it already published rather than
// inventing a transition.
const char* OfonoState(std::string_view s) {
    if (s == "dialing") return "dialing";
    if (s == "ringing") return "alerting";   // 180 from the far end
    if (s == "incoming") return "incoming";
    if (s == "active") return "active";
    if (s == "terminated") return "disconnected";
    return nullptr;
}

// imsd hangup reasons -> the three oFono DisconnectReason values.
const char* OfonoReason(std::string_view r) {
    if (r == "local-hangup") return "local";
    if (r == "remote-hangup" || r == "refused-or-busy") return "remote";
    return "network";
}

// ---- signal emission --------------------------------------------------------
void Emit(const char* path, const char* iface, const char* name, GVariant* params) {
    if (!Bus) return;
    g_dbus_connection_emit_signal(Bus, nullptr, path, iface, name, params, nullptr);
}

GVariant* PropChanged(const char* name, GVariant* value) {
    return g_variant_new("(sv)", name, value);
}

// ---- Modem / VoiceCallManager properties ------------------------------------
bool VoiceUp() { return ImsdUp && Registered; }

// Listed unconditionally: the plugin builds one Origin per appearance of
// this interface and cannot survive a re-appearance (header). Whether calls
// can actually be placed is VoiceUp(), checked in Dial and shown as Online.
GVariant* InterfacesValue() {
    GVariantBuilder b;
    g_variant_builder_init(&b, G_VARIANT_TYPE("as"));
    g_variant_builder_add(&b, "s", VcmIface);
    return g_variant_builder_end(&b);
}

GVariant* ModemProps() {
    GVariantBuilder b;
    g_variant_builder_init(&b, G_VARIANT_TYPE("a{sv}"));
    g_variant_builder_add(&b, "{sv}", "Name", g_variant_new_string("imsd"));
    g_variant_builder_add(&b, "{sv}", "Manufacturer", g_variant_new_string("Catcrafts"));
    g_variant_builder_add(&b, "{sv}", "Model", g_variant_new_string("userspace IMS"));
    g_variant_builder_add(&b, "{sv}", "Type", g_variant_new_string("hardware"));
    g_variant_builder_add(&b, "{sv}", "Powered", g_variant_new_boolean(TRUE));
    g_variant_builder_add(&b, "{sv}", "Online", g_variant_new_boolean(VoiceUp() ? TRUE : FALSE));
    g_variant_builder_add(&b, "{sv}", "Lockdown", g_variant_new_boolean(FALSE));
    g_variant_builder_add(&b, "{sv}", "Interfaces", InterfacesValue());
    return g_variant_builder_end(&b);
}

// EmergencyNumbers mirrors imsd's own configuration (builtin 112/911 plus the
// EMERGENCY_NUMBERS list) so an oFono client can display it. Classification
// itself is imsd's — it decides at Dial time whether a number becomes an
// urn:service:sos INVITE — and this list never gates a dial here.
GVariant* VcmProps() {
    GVariantBuilder b;
    g_variant_builder_init(&b, G_VARIANT_TYPE("a{sv}"));
    GVariantBuilder n;
    g_variant_builder_init(&n, G_VARIANT_TYPE("as"));
    for (const std::string& e : EmergencyNumbers)
        g_variant_builder_add(&n, "s", e.c_str());
    g_variant_builder_add(&b, "{sv}", "EmergencyNumbers", g_variant_builder_end(&n));
    return g_variant_builder_end(&b);
}

// oFono's StartTime is an ISO 8601 string; imsd hands us epoch seconds.
std::string StartTimeOf(std::int64_t epoch) {
    if (epoch <= 0) return "";
    GDateTime* dt = g_date_time_new_from_unix_utc(epoch);
    if (!dt) return "";
    gchar* s = g_date_time_format_iso8601(dt);
    std::string out = s ? s : "";
    g_free(s);
    g_date_time_unref(dt);
    return out;
}

// Deliberately no "Emergency" key: imsd owns emergency classification and does
// not export its per-call verdict, and a second guess here could disagree with
// the INVITE that actually went out.
GVariant* CallProps(const CallRec& r) {
    GVariantBuilder b;
    g_variant_builder_init(&b, G_VARIANT_TYPE("a{sv}"));
    g_variant_builder_add(&b, "{sv}", "LineIdentification", g_variant_new_string(r.number.c_str()));
    g_variant_builder_add(&b, "{sv}", "Name", g_variant_new_string(""));
    g_variant_builder_add(&b, "{sv}", "State", g_variant_new_string(r.state.c_str()));
    g_variant_builder_add(&b, "{sv}", "Multiparty", g_variant_new_boolean(FALSE));
    g_variant_builder_add(&b, "{sv}", "RemoteHeld", g_variant_new_boolean(FALSE));
    g_variant_builder_add(&b, "{sv}", "RemoteMultiparty", g_variant_new_boolean(FALSE));
    std::string started = StartTimeOf(r.startedAt);
    if (!started.empty())
        g_variant_builder_add(&b, "{sv}", "StartTime", g_variant_new_string(started.c_str()));
    return g_variant_builder_end(&b);
}

GVariant* CallVector() {
    GVariantBuilder b;
    g_variant_builder_init(&b, G_VARIANT_TYPE("a(oa{sv})"));
    for (const auto& [uni, rec] : Calls)
        g_variant_builder_add(&b, "(o@a{sv})", rec.path.c_str(), CallProps(rec));
    return g_variant_builder_end(&b);
}

// ---- imsd (client side) -----------------------------------------------------
// Fire-and-forget into imsd; a dead imsd shows up via the name watch, so a
// failure here is logged rather than fatal (same contract as imsd-dialerd).
void CallImsd(const char* method, GVariant* params) {
    g_dbus_connection_call(
        Imsd, ImsdBus, ImsdPath, ImsdIface, method, params, nullptr,
        G_DBUS_CALL_FLAGS_NONE, 5000, nullptr,
        [](GObject* src, GAsyncResult* res, gpointer m) {
            GError* err = nullptr;
            GVariant* r = g_dbus_connection_call_finish(G_DBUS_CONNECTION(src), res, &err);
            if (err) {
                Log(std::format("{} failed: {}", static_cast<const char*>(m), err->message));
                g_error_free(err);
            }
            if (r) g_variant_unref(r);
        },
        const_cast<char*>(method));
}

std::string ActiveUni() {
    for (const auto& [uni, rec] : Calls)
        if (rec.state != "disconnected") return uni;
    return "";
}

// ---- the VoiceCall objects --------------------------------------------------
constexpr const char* CallXml = R"xml(
<node>
  <interface name="org.ofono.VoiceCall">
    <method name="GetProperties"><arg type="a{sv}" name="properties" direction="out"/></method>
    <method name="Deflect"><arg type="s" name="number" direction="in"/></method>
    <method name="Hangup"/>
    <method name="Answer"/>
    <signal name="PropertyChanged"><arg type="s" name="name"/><arg type="v" name="value"/></signal>
    <signal name="DisconnectReason"><arg type="s" name="reason"/></signal>
  </interface>
</node>)xml";

void HandleCallMethod(GDBusConnection*, const gchar*, const gchar* objectPath, const gchar*, const gchar* method, GVariant*, GDBusMethodInvocation* inv, gpointer) {
    auto byPath = ByPath.find(objectPath ? objectPath : "");
    if (byPath == ByPath.end()) {
        g_dbus_method_invocation_return_dbus_error(inv, ErrFailed, "no such call");
        return;
    }
    auto it = Calls.find(byPath->second);
    if (it == Calls.end()) {
        g_dbus_method_invocation_return_dbus_error(inv, ErrFailed, "no such call");
        return;
    }
    std::string_view m = method;
    if (m == "GetProperties") {
        g_dbus_method_invocation_return_value(inv, g_variant_new("(@a{sv})", CallProps(it->second)));
        return;
    }
    if (m == "Answer") {
        Log(std::format("Answer({})", it->second.uni));
        CallImsd("Accept", g_variant_new("(s)", it->second.uni.c_str()));
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "Hangup") {
        Log(std::format("Hangup({})", it->second.uni));
        CallImsd("HangUp", g_variant_new("(s)", it->second.uni.c_str()));
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "Deflect") {
        g_dbus_method_invocation_return_dbus_error(inv, ErrNotImplemented, "call deflection is not implemented");
        return;
    }
    g_dbus_method_invocation_return_dbus_error(inv, "org.freedesktop.DBus.Error.UnknownMethod", "no such method");
}

bool IsRead(std::string_view method) { return method == "GetProperties" || method == "GetCalls" || method == "GetModems"; }
constexpr char ErrAccessDenied[] = "org.ofono.Error.AccessDenied";
const GDBusInterfaceVTable CallVtable = { imsd::bus::Gated<HandleCallMethod, IsRead, ErrAccessDenied, Log>, nullptr, nullptr, {} };

guint RegisterObject(const char* xml, const char* path, const GDBusInterfaceVTable* vtable) {
    GDBusNodeInfo* node = g_dbus_node_info_new_for_xml(xml, nullptr);
    guint id = g_dbus_connection_register_object(Bus, path, node->interfaces[0], vtable, nullptr, nullptr, nullptr);
    g_dbus_node_info_unref(node);
    return id;
}

// Allocate (or recall) the object path for an imsd call uni. imsd's unis
// ("ims-call-3") are not valid object-path elements, and a Dial reply can beat
// the CallAdded signal, so the mapping is created on first mention either way.
const std::string& PathFor(const std::string& uni) {
    auto it = UniToPath.find(uni);
    if (it != UniToPath.end()) return it->second;
    std::string path = std::format("{}/voicecall{:02}", ModemPath, ++PathSeq);
    ByPath[path] = uni;
    return UniToPath.emplace(uni, std::move(path)).first->second;
}

void ForgetPath(const std::string& uni) {
    auto it = UniToPath.find(uni);
    if (it == UniToPath.end()) return;
    ByPath.erase(it->second);
    UniToPath.erase(it);
}

void AddCall(const std::string& uni, const std::string& number, std::string_view imsdState, std::int64_t startedAt) {
    if (uni.empty() || Calls.contains(uni)) return;
    const char* st = OfonoState(imsdState);
    CallRec rec;
    rec.uni = uni;
    rec.path = PathFor(uni);
    rec.number = number;
    rec.state = st ? st : "dialing";
    rec.inbound = rec.state == "incoming";
    rec.startedAt = startedAt ? startedAt : g_get_real_time() / G_USEC_PER_SEC;
    rec.regId = RegisterObject(CallXml, rec.path.c_str(), &CallVtable);
    Log(std::format("call added {} {} {} [{}]", rec.path, rec.inbound ? "<-" : "->", rec.number, rec.state));
    GVariant* props = CallProps(rec);
    Calls[uni] = std::move(rec);
    Emit(ModemPath, VcmIface, "CallAdded", g_variant_new("(o@a{sv})", Calls[uni].path.c_str(), props));
}

void SetCallState(CallRec& rec, const char* state) {
    if (rec.state == state) return;
    rec.state = state;
    Emit(rec.path.c_str(), CallIface, "PropertyChanged",
         PropChanged("State", g_variant_new_string(state)));
}

// The plugin caches DisconnectReason and reads it when CallRemoved lands, so
// the order here is load-bearing: disconnected state, then reason, then removal.
void RemoveCall(const std::string& uni, const char* reasonHint) {
    auto it = Calls.find(uni);
    if (it == Calls.end()) return;
    CallRec& rec = it->second;
    SetCallState(rec, "disconnected");
    const char* reason = !rec.reason.empty() ? rec.reason.c_str()
                                             : (reasonHint ? reasonHint : "network");
    Emit(rec.path.c_str(), CallIface, "DisconnectReason", g_variant_new("(s)", reason));
    if (rec.regId) g_dbus_connection_unregister_object(Bus, rec.regId);
    std::string path = rec.path;
    Log(std::format("call removed {} ({})", path, reason));
    Calls.erase(it);
    ForgetPath(uni);
    Emit(ModemPath, VcmIface, "CallRemoved", g_variant_new("(o)", path.c_str()));
}

// ---- Manager / Modem / VoiceCallManager objects -----------------------------
constexpr const char* ManagerXml = R"xml(
<node>
  <interface name="org.ofono.Manager">
    <method name="GetModems"><arg type="a(oa{sv})" name="modems" direction="out"/></method>
    <signal name="ModemAdded"><arg type="o" name="path"/><arg type="a{sv}" name="properties"/></signal>
    <signal name="ModemRemoved"><arg type="o" name="path"/></signal>
  </interface>
</node>)xml";

constexpr const char* ModemXml = R"xml(
<node>
  <interface name="org.ofono.Modem">
    <method name="GetProperties"><arg type="a{sv}" name="properties" direction="out"/></method>
    <method name="SetProperty">
      <arg type="s" name="property" direction="in"/>
      <arg type="v" name="value" direction="in"/>
    </method>
    <signal name="PropertyChanged"><arg type="s" name="name"/><arg type="v" name="value"/></signal>
  </interface>
</node>)xml";

constexpr const char* VcmXml = R"xml(
<node>
  <interface name="org.ofono.VoiceCallManager">
    <method name="GetProperties"><arg type="a{sv}" name="properties" direction="out"/></method>
    <method name="Dial">
      <arg type="s" name="number" direction="in"/>
      <arg type="s" name="hide_callerid" direction="in"/>
      <arg type="o" name="path" direction="out"/>
    </method>
    <method name="GetCalls"><arg type="a(oa{sv})" name="calls_with_properties" direction="out"/></method>
    <method name="HangupAll"/>
    <method name="SendTones"><arg type="s" name="tones" direction="in"/></method>
    <method name="Transfer"/>
    <method name="SwapCalls"/>
    <method name="ReleaseAndAnswer"/>
    <method name="ReleaseAndSwap"/>
    <method name="HoldAndAnswer"/>
    <method name="PrivateChat">
      <arg type="o" name="call" direction="in"/>
      <arg type="ao" name="calls" direction="out"/>
    </method>
    <method name="CreateMultiparty"><arg type="ao" name="calls" direction="out"/></method>
    <method name="HangupMultiparty"/>
    <method name="DialLast"/>
    <method name="DialMemory"><arg type="u" name="memory_location" direction="in"/></method>
    <signal name="PropertyChanged"><arg type="s" name="name"/><arg type="v" name="value"/></signal>
    <signal name="CallAdded"><arg type="o" name="path"/><arg type="a{sv}" name="properties"/></signal>
    <signal name="CallRemoved"><arg type="o" name="path"/></signal>
    <signal name="Forwarded"><arg type="s" name="type"/></signal>
    <signal name="BarringActive"><arg type="s" name="type"/></signal>
  </interface>
</node>)xml";

// USSD has no path through the IMS stack. The interface is answered (Calls
// proxies it unconditionally, and a legible error beats UnknownInterface) but
// deliberately NOT advertised in the modem's Interfaces list.
constexpr const char* SsXml = R"xml(
<node>
  <interface name="org.ofono.SupplementaryServices">
    <method name="Initiate">
      <arg type="s" name="command" direction="in"/>
      <arg type="s" name="result_name" direction="out"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Respond">
      <arg type="s" name="reply" direction="in"/>
      <arg type="s" name="result" direction="out"/>
    </method>
    <method name="Cancel"/>
    <method name="GetProperties"><arg type="a{sv}" name="properties" direction="out"/></method>
    <signal name="NotificationReceived"><arg type="s" name="message"/></signal>
    <signal name="RequestReceived"><arg type="s" name="message"/></signal>
    <signal name="PropertyChanged"><arg type="s" name="name"/><arg type="v" name="value"/></signal>
  </interface>
</node>)xml";

void HandleManagerMethod(GDBusConnection*, const gchar*, const gchar*, const gchar*, const gchar* method, GVariant*, GDBusMethodInvocation* inv, gpointer) {
    if (std::string_view(method) == "GetModems") {
        GVariantBuilder b;
        g_variant_builder_init(&b, G_VARIANT_TYPE("a(oa{sv})"));
        g_variant_builder_add(&b, "(o@a{sv})", ModemPath, ModemProps());
        g_dbus_method_invocation_return_value(inv, g_variant_new("(@a(oa{sv}))", g_variant_builder_end(&b)));
        return;
    }
    g_dbus_method_invocation_return_dbus_error(inv, "org.freedesktop.DBus.Error.UnknownMethod", "no such method");
}

void HandleModemMethod(GDBusConnection*, const gchar*, const gchar*, const gchar*, const gchar* method, GVariant* params, GDBusMethodInvocation* inv, gpointer) {
    std::string_view m = method;
    if (m == "GetProperties") {
        g_dbus_method_invocation_return_value(inv, g_variant_new("(@a{sv})", ModemProps()));
        return;
    }
    if (m == "SetProperty") {
        const gchar* prop = nullptr;
        GVariant* value = nullptr;
        g_variant_get(params, "(&sv)", &prop, &value);
        if (value) g_variant_unref(value);
        // Powered/Online track imsd's registration; nothing here is settable.
        g_dbus_method_invocation_return_dbus_error(
            inv, ErrNotSupported,
            std::format("{} is driven by imsd's registration and cannot be set", prop ? prop : "?").c_str());
        return;
    }
    g_dbus_method_invocation_return_dbus_error(inv, "org.freedesktop.DBus.Error.UnknownMethod", "no such method");
}

void OnDialReply(GObject* src, GAsyncResult* res, gpointer user) {
    GDBusMethodInvocation* inv = static_cast<GDBusMethodInvocation*>(user);
    GError* err = nullptr;
    GVariant* r = g_dbus_connection_call_finish(G_DBUS_CONNECTION(src), res, &err);
    if (!r) {
        Log(std::format("imsd Dial failed: {}", err ? err->message : "?"));
        g_dbus_method_invocation_return_dbus_error(inv, ErrFailed, err ? err->message : "imsd Dial failed");
        if (err) g_error_free(err);
        return;
    }
    const gchar* uni = nullptr;
    g_variant_get(r, "(&s)", &uni);
    if (!uni || !*uni) {
        g_variant_unref(r);
        g_dbus_method_invocation_return_dbus_error(inv, ErrFailed, "imsd returned no call id");
        return;
    }
    const std::string& path = PathFor(uni);
    g_dbus_method_invocation_return_value(inv, g_variant_new("(o)", path.c_str()));
    g_variant_unref(r);
}

void HandleVcmMethod(GDBusConnection*, const gchar*, const gchar*, const gchar*, const gchar* method, GVariant* params, GDBusMethodInvocation* inv, gpointer) {
    std::string_view m = method;
    if (m == "GetProperties") {
        g_dbus_method_invocation_return_value(inv, g_variant_new("(@a{sv})", VcmProps()));
        return;
    }
    if (m == "GetCalls") {
        g_dbus_method_invocation_return_value(inv, g_variant_new("(@a(oa{sv}))", CallVector()));
        return;
    }
    if (m == "Dial") {
        const gchar* number = nullptr;
        const gchar* clir = nullptr;
        g_variant_get(params, "(&s&s)", &number, &clir);
        if (!number || !*number) {
            g_dbus_method_invocation_return_dbus_error(inv, ErrInvalidArgs, "empty number");
            return;
        }
        // "default" is the only CLIR setting imsd can honour. Failing loudly on
        // a withhold request beats dialling with the caller ID exposed.
        std::string_view hide = clir ? clir : "";
        if (!hide.empty() && hide != "default") {
            g_dbus_method_invocation_return_dbus_error(inv, ErrNotImplemented, "per-call caller-ID control is not implemented");
            return;
        }
        if (!VoiceUp()) {
            g_dbus_method_invocation_return_dbus_error(inv, ErrFailed, ImsdUp ? "imsd is not registered" : "imsd is not running");
            return;
        }
        Log(std::format("Dial({})", number));
        g_dbus_connection_call(Imsd, ImsdBus, ImsdPath, ImsdIface, "Dial",
                               g_variant_new("(s)", number), G_VARIANT_TYPE("(s)"),
                               G_DBUS_CALL_FLAGS_NONE, 10000, nullptr, OnDialReply, inv);
        return;
    }
    if (m == "HangupAll") {
        for (const auto& [uni, rec] : Calls)
            if (rec.state != "disconnected")
                CallImsd("HangUp", g_variant_new("(s)", uni.c_str()));
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "SendTones") {
        const gchar* tones = nullptr;
        g_variant_get(params, "(&s)", &tones);
        std::string target = ActiveUni();
        if (target.empty()) {
            g_dbus_method_invocation_return_dbus_error(inv, ErrFailed, "no active call");
            return;
        }
        CallImsd("SendDtmf", g_variant_new("(ss)", target.c_str(), tones ? tones : ""));
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    // Single-call stack: no hold, no multiparty, no transfer, no dial memory.
    if (m == "Transfer" || m == "SwapCalls" || m == "ReleaseAndAnswer" || m == "ReleaseAndSwap"
        || m == "HoldAndAnswer" || m == "PrivateChat" || m == "CreateMultiparty"
        || m == "HangupMultiparty" || m == "DialLast" || m == "DialMemory") {
        g_dbus_method_invocation_return_dbus_error(inv, ErrNotImplemented, "not implemented by imsd");
        return;
    }
    g_dbus_method_invocation_return_dbus_error(inv, "org.freedesktop.DBus.Error.UnknownMethod", "no such method");
}

void HandleSsMethod(GDBusConnection*, const gchar*, const gchar*, const gchar*, const gchar* method, GVariant*, GDBusMethodInvocation* inv, gpointer) {
    std::string_view m = method;
    if (m == "GetProperties") {
        GVariantBuilder b;
        g_variant_builder_init(&b, G_VARIANT_TYPE("a{sv}"));
        g_variant_builder_add(&b, "{sv}", "State", g_variant_new_string("idle"));
        g_dbus_method_invocation_return_value(inv, g_variant_new("(@a{sv})", g_variant_builder_end(&b)));
        return;
    }
    if (m == "Cancel") {
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    g_dbus_method_invocation_return_dbus_error(inv, ErrNotSupported, "USSD is not supported on the IMS stack");
}

const GDBusInterfaceVTable ManagerVtable = { HandleManagerMethod, nullptr, nullptr, {} };
const GDBusInterfaceVTable ModemVtable = { imsd::bus::Gated<HandleModemMethod, IsRead, ErrAccessDenied, Log>, nullptr, nullptr, {} };
const GDBusInterfaceVTable VcmVtable = { imsd::bus::Gated<HandleVcmMethod, IsRead, ErrAccessDenied, Log>, nullptr, nullptr, {} };
const GDBusInterfaceVTable SsVtable = { imsd::bus::Gated<HandleSsMethod, IsRead, ErrAccessDenied, Log>, nullptr, nullptr, {} };

// ---- imsd events ------------------------------------------------------------
void PublishVoiceState() {
    Emit(ModemPath, ModemIface, "PropertyChanged",
         PropChanged("Online", g_variant_new_boolean(VoiceUp() ? TRUE : FALSE)));
    Log(std::format("voice {}", VoiceUp() ? "registered" : (ImsdUp ? "unregistered" : "unavailable (imsd down)")));
}

void OnImsdSignal(GDBusConnection*, const gchar*, const gchar*, const gchar*, const gchar* signal, GVariant* params, gpointer) {
    std::string_view sig = signal;
    if (sig == "CallAdded") {
        const gchar* uni = nullptr;
        GVariant* info = nullptr;
        g_variant_get(params, "(&s@a{sv})", &uni, &info);
        AddCall(uni ? uni : "", DictStr(info, "number"),
                DictStr(info, "state").empty()
                    ? (DictStr(info, "direction") == "incoming" ? "incoming" : "dialing")
                    : DictStr(info, "state"),
                DictInt64(info, "startedAt"));
        g_variant_unref(info);
    } else if (sig == "CallStateChanged") {
        const gchar* uni = nullptr;
        const gchar* state = nullptr;
        const gchar* reason = nullptr;
        g_variant_get(params, "(&s&s&s)", &uni, &state, &reason);
        auto it = Calls.find(uni ? uni : "");
        if (it == Calls.end()) return;
        const char* mapped = OfonoState(state ? state : "");
        if (!mapped) return;
        if (std::string_view(mapped) == "disconnected") {
            // Bank the reason before the state flips: RemoveCall emits it.
            it->second.reason = OfonoReason(reason ? reason : "");
            SetCallState(it->second, mapped);
            Log(std::format("{} -> disconnected ({})", it->second.path, it->second.reason));
            return;
        }
        Log(std::format("{} -> {}", it->second.path, mapped));
        SetCallState(it->second, mapped);
    } else if (sig == "CallDeleted") {
        const gchar* uni = nullptr;
        g_variant_get(params, "(&s)", &uni);
        RemoveCall(uni ? uni : "", "network");
    } else if (sig == "RegistrationChanged") {
        gboolean reg = FALSE;
        g_variant_get(params, "(b)", &reg);
        if (Registered == static_cast<bool>(reg)) return;
        Registered = reg;
        PublishVoiceState();
    }
}

void RefreshFromImsd() {
    GError* err = nullptr;
    GVariant* st = g_dbus_connection_call_sync(Imsd, ImsdBus, ImsdPath, ImsdIface, "GetStatus", nullptr, G_VARIANT_TYPE("(a{sv})"), G_DBUS_CALL_FLAGS_NONE, 5000, nullptr, &err);
    if (!st) {
        Log(std::format("imsd status query failed: {}", err ? err->message : "?"));
        if (err) g_error_free(err);
        Registered = false;
        return;
    }
    GVariant* dict = g_variant_get_child_value(st, 0);
    Registered = DictBool(dict, "registered");
    g_variant_unref(dict);
    g_variant_unref(st);

    GVariant* calls = g_dbus_connection_call_sync(Imsd, ImsdBus, ImsdPath, ImsdIface, "GetCalls", nullptr, G_VARIANT_TYPE("(aa{sv})"), G_DBUS_CALL_FLAGS_NONE, 5000, nullptr, &err);
    if (!calls) {
        if (err) g_error_free(err);
        return;
    }
    GVariant* arr = g_variant_get_child_value(calls, 0);
    GVariantIter it;
    g_variant_iter_init(&it, arr);
    while (GVariant* info = g_variant_iter_next_value(&it)) {
        AddCall(DictStr(info, "uni"), DictStr(info, "number"), DictStr(info, "state"), DictInt64(info, "startedAt"));
        g_variant_unref(info);
    }
    g_variant_unref(arr);
    g_variant_unref(calls);
}

void OnImsdAppeared(GDBusConnection*, const gchar*, const gchar*, gpointer) {
    if (ImsdUp) return;
    ImsdUp = true;
    Log("imsd up");
    RefreshFromImsd();
    PublishVoiceState();
}

void OnImsdVanished(GDBusConnection*, const gchar*, gpointer) {
    if (!ImsdUp) return;
    ImsdUp = false;
    Registered = false;
    Log("imsd down");
    std::vector<std::string> live;
    for (const auto& [uni, rec] : Calls) live.push_back(uni);
    for (const std::string& uni : live) RemoveCall(uni, "network");
    PublishVoiceState();
}

gboolean OnTerm(gpointer loop) {
    g_main_loop_quit(static_cast<GMainLoop*>(loop));
    return G_SOURCE_REMOVE;
}

void LoadEmergencyNumbers() {
    EmergencyNumbers = { "112", "911" };
    std::string extra = EnvOr("EMERGENCY_NUMBERS", "");
    for (std::size_t pos = 0; pos <= extra.size();) {
        std::size_t comma = extra.find(',', pos);
        std::size_t end = comma == std::string::npos ? extra.size() : comma;
        std::string n = extra.substr(pos, end - pos);
        std::erase(n, ' ');
        if (!n.empty() && std::ranges::find(EmergencyNumbers, n) == EmergencyNumbers.end())
            EmergencyNumbers.push_back(std::move(n));
        if (comma == std::string::npos) break;
        pos = comma + 1;
    }
}

}  // namespace

int main(int argc, char** argv) {
    bool session = false;
    for (int i = 1; i < argc; i++)
        if (std::string_view(argv[i]) == "--session") session = true;

    GBusType ofonoBus = session ? G_BUS_TYPE_SESSION : G_BUS_TYPE_SYSTEM;
    imsd::bus::AuthorizeCalls = !session;
    std::string imsdBusName = EnvOr("IMSD_BUS", session ? "session" : "system");
    GBusType imsdBus = imsdBusName == "session" ? G_BUS_TYPE_SESSION : G_BUS_TYPE_SYSTEM;

    LoadEmergencyNumbers();

    GError* err = nullptr;
    Bus = g_bus_get_sync(ofonoBus, nullptr, &err);
    if (!Bus) {
        std::println(std::cerr, "imsd-ofonod: no {} bus: {}", session ? "session" : "system", err ? err->message : "?");
        return 1;
    }
    Imsd = imsdBus == ofonoBus ? Bus : g_bus_get_sync(imsdBus, nullptr, &err);
    if (!Imsd) {
        std::println(std::cerr, "imsd-ofonod: no {} bus for imsd: {}", imsdBusName, err ? err->message : "?");
        return 1;
    }

    RegisterObject(ManagerXml, ManagerPath, &ManagerVtable);
    RegisterObject(ModemXml, ModemPath, &ModemVtable);
    RegisterObject(VcmXml, ModemPath, &VcmVtable);
    RegisterObject(SsXml, ModemPath, &SsVtable);

    Loop = g_main_loop_new(nullptr, FALSE);

    // DO_NOT_QUEUE: a real ofonod already owning the name means two stacks are
    // fighting over the modem — fail loudly instead of waiting our turn.
    guint owner = g_bus_own_name_on_connection(
        Bus, OfonoBus, G_BUS_NAME_OWNER_FLAGS_DO_NOT_QUEUE,
        [](GDBusConnection*, const gchar* n, gpointer) { Log(std::format("owning {}", n)); },
        [](GDBusConnection*, const gchar* n, gpointer lp) {
            Log(std::format("lost {} (is ofonod running?) — exiting", n));
            g_main_loop_quit(static_cast<GMainLoop*>(lp));
        },
        Loop, nullptr);

    g_dbus_connection_signal_subscribe(Imsd, ImsdBus, ImsdIface, nullptr, ImsdPath, nullptr, G_DBUS_SIGNAL_FLAGS_NONE, OnImsdSignal, nullptr, nullptr);
    g_bus_watch_name_on_connection(Imsd, ImsdBus, G_BUS_NAME_WATCHER_FLAGS_NONE, OnImsdAppeared, OnImsdVanished, nullptr, nullptr);

    g_unix_signal_add(SIGTERM, OnTerm, Loop);
    g_unix_signal_add(SIGINT, OnTerm, Loop);

    Log(std::format("up (org.ofono on the {} bus, imsd on the {} bus)", session ? "session" : "system", imsdBusName));
    g_main_loop_run(Loop);
    g_bus_unown_name(owner);
    g_main_loop_unref(Loop);
    return 0;
}
