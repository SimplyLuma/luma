// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types no-char-pointer
/*
imsd-dialerd — Plasma Dialer backend for imsd.

Drop-in replacement for plasma-dialer's `modem-daemon` (the only component of
the Plasma Mobile telephony stack that talks to ModemManager). Owns the same
session-bus names/objects the stock daemon owns:

    org.kde.telephony.CallUtils    /org/kde/telephony/CallUtils/tel/mm
    org.kde.telephony.DeviceUtils  /org/kde/telephony/DeviceUtils/tel/mm
    org.kde.telephony.UssdUtils    /org/kde/telephony/UssdUtils/tel/mm

(the `/tel/mm` path suffix is hardcoded in plasma-dialer's QML plugin and in
kde-telephony-daemon — keep it) and bridges them to imsd (net.catcrafts.IMS1
on the system bus), which drives the real SIP/IPsec/RTP stack. plasma-dialer,
kde-telephony-daemon (ringing/notifications/call history/callaudiod/MPRIS)
and spacebar run UNMODIFIED; the stock modem-daemon autostart must be
disabled (Hidden=true override) so we own the names.

ABI notes (plasma-dialer 6.7.3, verified against kde-telephony-meta source):
  - CallData is marshalled as a STRUCT WRAPPING a{sv} — actual signature
    "(a{sv})" — even though the interface XML declares (sssss(i)(i)(i)ixi).
    Keys: id protocol provider account communicationWith direction state
    stateReason callAttemptDuration startedAt(int64 epoch secs) duration.
  - enums are struct-wrapped int32s "(i)"; values = MMCallState numbering.
  - sendDtmf/accept/hangUp can arrive with EMPTY callUni (ActiveCallModel) ->
    map an empty callUni to the active call.
  - on callAdded/callDeleted the UI re-fetches: fetchCalls() must emit
    callsChanged with the full current vector (live-computed durations —
    the UI's 1 s timer only interpolates client-side).
  - kde-telephony-daemon logs history on callStateChanged(state==Terminated)
    and drives callaudiod on Active/Terminated.

Runs as the session user (autostart .desktop), no root needed. Everything
happens on the GLib main loop: imsd's system-bus signals and the dialer's
session-bus method calls both arrive there, so there is no locking.
*/

#include <gio/gio.h>
#include <glib-unix.h>

import std;

namespace {

constexpr const char* ImsdBus = "net.catcrafts.IMS1";
constexpr const char* ImsdPath = "/net/catcrafts/IMS1";
constexpr const char* ImsdIface = "net.catcrafts.IMS1";

constexpr const char* CallsIface = "org.kde.telephony.CallUtils";
constexpr const char* DeviceIface = "org.kde.telephony.DeviceUtils";
constexpr const char* UssdIface = "org.kde.telephony.UssdUtils";
constexpr const char* CallsPath = "/org/kde/telephony/CallUtils/tel/mm";
constexpr const char* DevicePath = "/org/kde/telephony/DeviceUtils/tel/mm";
constexpr const char* UssdPath = "/org/kde/telephony/UssdUtils/tel/mm";

constexpr const char* DeviceUni = "ims";
constexpr const char* Provider = "20408";  // operator id, as modem-daemon reports it

// DialerTypes::CallState (== MMCallState numbering)
constexpr int StDialing = 1, StRingingOut = 2, StRingingIn = 3,
              StActive = 4, StTerminated = 7;
// DialerTypes::CallStateReason
constexpr int RsUnknown = 0, RsOutgoing = 1, RsIncoming = 2, RsAccepted = 3,
              RsTerminated = 4, RsBusy = 5, RsError = 6;
// DialerTypes::CallDirection
constexpr int DirIncoming = 1, DirOutgoing = 2;

void Log(std::string_view m) { std::println("imsd-dialerd: {}", m); std::fflush(stdout); }

int StateOf(std::string_view s, int fallback) {
    if (s == "dialing") return StDialing;
    if (s == "ringing") return StRingingOut;
    if (s == "incoming") return StRingingIn;
    if (s == "active") return StActive;
    if (s == "terminated") return StTerminated;
    return fallback;
}
int ReasonOf(std::string_view s, int fallback) {
    if (s == "outgoing") return RsOutgoing;
    if (s == "incoming") return RsIncoming;
    if (s == "accepted") return RsAccepted;
    if (s == "local-hangup" || s == "remote-hangup") return RsTerminated;
    if (s == "refused-or-busy") return RsBusy;
    if (s == "error") return RsError;
    return fallback;
}

std::int64_t NowEpoch() {
    return std::chrono::duration_cast<std::chrono::seconds>(std::chrono::system_clock::now().time_since_epoch()).count();
}

struct CallRec {
    std::string uni, number;
    int direction = DirOutgoing;
    int state = StDialing;
    int reason = RsOutgoing;
    std::int64_t startedAt = 0;
    std::int64_t answeredAt = 0;   // 0 = not answered
    std::int64_t Duration() const {
        return answeredAt ? NowEpoch() - answeredAt : 0;
    }
};

// ---- state (main thread only) ----------------------------------------------
GDBusConnection* Session = nullptr;
GDBusConnection* System = nullptr;
std::map<std::string, CallRec> Calls;   // uni -> record
std::string Account;                    // MSISDN from imsd (history 'account')
bool Registered = false;
bool ImsdUp = false;
GMainLoop* Loop = nullptr;

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

GVariant* Enum(int v) { return g_variant_new("(i)", v); }

// The (a{sv}) CallData struct (see the ABI notes up top).
GVariant* CallData(const CallRec& r) {
    GVariantBuilder b;
    g_variant_builder_init(&b, G_VARIANT_TYPE("a{sv}"));
    g_variant_builder_add(&b, "{sv}", "id", g_variant_new_string(r.uni.c_str()));
    g_variant_builder_add(&b, "{sv}", "protocol", g_variant_new_string("tel"));
    g_variant_builder_add(&b, "{sv}", "provider", g_variant_new_string(Provider));
    g_variant_builder_add(&b, "{sv}", "account", g_variant_new_string(Account.c_str()));
    g_variant_builder_add(&b, "{sv}", "communicationWith", g_variant_new_string(r.number.c_str()));
    g_variant_builder_add(&b, "{sv}", "direction", g_variant_new_int32(r.direction));
    g_variant_builder_add(&b, "{sv}", "state", g_variant_new_int32(r.state));
    g_variant_builder_add(&b, "{sv}", "stateReason", g_variant_new_int32(r.reason));
    g_variant_builder_add(&b, "{sv}", "callAttemptDuration", g_variant_new_int32(0));
    g_variant_builder_add(&b, "{sv}", "startedAt", g_variant_new_int64(r.startedAt));
    g_variant_builder_add(&b, "{sv}", "duration", g_variant_new_int32(static_cast<int>(r.Duration())));
    return g_variant_new("(@a{sv})", g_variant_builder_end(&b));
}

GVariant* CallVector() {
    GVariantBuilder b;
    g_variant_builder_init(&b, G_VARIANT_TYPE("a(a{sv})"));
    for (auto& [uni, rec] : Calls)
        g_variant_builder_add_value(&b, CallData(rec));
    return g_variant_builder_end(&b);
}

GVariant* DeviceList() {
    GVariantBuilder b;
    g_variant_builder_init(&b, G_VARIANT_TYPE("as"));
    if (ImsdUp && Registered) g_variant_builder_add(&b, "s", DeviceUni);
    return g_variant_builder_end(&b);
}

std::string ActiveUni() {
    for (auto& [uni, rec] : Calls)
        if (rec.state != StTerminated) return uni;
    return "";
}

void EmitCalls(const char* name, GVariant* params) {
    g_dbus_connection_emit_signal(Session, nullptr, CallsPath, CallsIface, name, params, nullptr);
}
void EmitDevice(const char* name, GVariant* params) {
    g_dbus_connection_emit_signal(Session, nullptr, DevicePath, DeviceIface, name, params, nullptr);
}
void EmitUssd(const char* name, GVariant* params) {
    g_dbus_connection_emit_signal(Session, nullptr, UssdPath, UssdIface, name, params, nullptr);
}

// ---- imsd (system bus) side -------------------------------------------------
// Fire-and-forget method call into imsd; failures are logged, not fatal
// (matching the Python bridge — a dead imsd shows up via the name watch).
void CallImsd(const char* method, GVariant* params) {
    g_dbus_connection_call(
        System, ImsdBus, ImsdPath, ImsdIface, method, params, nullptr,
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

void RefreshStatus() {
    GError* err = nullptr;
    GVariant* st = g_dbus_connection_call_sync(System, ImsdBus, ImsdPath, ImsdIface, "GetStatus", nullptr, G_VARIANT_TYPE("(a{sv})"), G_DBUS_CALL_FLAGS_NONE, 5000, nullptr, &err);
    if (err) {
        Log(std::format("imsd status query failed: {}", err->message));
        g_error_free(err);
        Registered = false;
        return;
    }
    GVariant* dict = g_variant_get_child_value(st, 0);
    Registered = DictBool(dict, "registered");
    std::string ppi = DictStr(dict, "ppi");
    if (ppi.starts_with("tel:")) Account = ppi.substr(4);
    g_variant_unref(dict);
    g_variant_unref(st);

    GVariant* calls = g_dbus_connection_call_sync(System, ImsdBus, ImsdPath, ImsdIface, "GetCalls", nullptr, G_VARIANT_TYPE("(aa{sv})"), G_DBUS_CALL_FLAGS_NONE, 5000, nullptr, &err);
    if (err) { g_error_free(err); return; }
    GVariant* arr = g_variant_get_child_value(calls, 0);
    GVariantIter it;
    g_variant_iter_init(&it, arr);
    while (GVariant* info = g_variant_iter_next_value(&it)) {
        std::string uni = DictStr(info, "uni");
        if (!uni.empty() && !Calls.contains(uni)) {
            CallRec rec;
            rec.uni = uni;
            rec.number = DictStr(info, "number");
            rec.direction = DictStr(info, "direction") == "incoming" ? DirIncoming
                                                                     : DirOutgoing;
            rec.state = StateOf(DictStr(info, "state"), StDialing);
            rec.reason = ReasonOf(DictStr(info, "reason"), RsUnknown);
            rec.startedAt = DictInt64(info, "startedAt");
            rec.answeredAt = DictInt64(info, "answeredAt");
            Calls[uni] = rec;
        }
        g_variant_unref(info);
    }
    g_variant_unref(arr);
    g_variant_unref(calls);
}

// imsd signals -> CallUtils signals
void OnImsdSignal(GDBusConnection*, const gchar*, const gchar*, const gchar*, const gchar* signal, GVariant* params, gpointer) {
    std::string_view sig = signal;
    if (sig == "CallAdded") {
        const gchar* cuni = nullptr;
        GVariant* info = nullptr;
        g_variant_get(params, "(&s@a{sv})", &cuni, &info);
        CallRec rec;
        rec.uni = cuni ? cuni : "";
        rec.number = DictStr(info, "number");
        rec.direction = DictStr(info, "direction") == "incoming" ? DirIncoming : DirOutgoing;
        rec.state = StateOf(DictStr(info, "state"), rec.direction == DirIncoming ? StRingingIn : StDialing);
        rec.reason = ReasonOf(DictStr(info, "reason"), rec.direction == DirIncoming ? RsIncoming : RsOutgoing);
        rec.startedAt = DictInt64(info, "startedAt");
        if (!rec.startedAt) rec.startedAt = NowEpoch();
        g_variant_unref(info);
        Calls[rec.uni] = rec;
        Log(std::format("call added {} {} {}", rec.uni, rec.direction == DirIncoming ? "<-" : "->", rec.number));
        EmitCalls("callAdded", g_variant_new("(ss@(i)@(i)@(i)s)", DeviceUni, rec.uni.c_str(), Enum(rec.direction), Enum(rec.state), Enum(rec.reason), rec.number.c_str()));
    } else if (sig == "CallStateChanged") {
        const gchar* cuni = nullptr; const gchar* cstate = nullptr;
        const gchar* creason = nullptr;
        g_variant_get(params, "(&s&s&s)", &cuni, &cstate, &creason);
        auto it = Calls.find(cuni ? cuni : "");
        if (it == Calls.end()) return;
        CallRec& rec = it->second;
        rec.state = StateOf(cstate ? cstate : "", rec.state);
        rec.reason = ReasonOf(creason ? creason : "", rec.reason);
        if (rec.state == StActive && !rec.answeredAt) rec.answeredAt = NowEpoch();
        Log(std::format("{} -> {} ({})", rec.uni, cstate, creason));
        EmitCalls("callStateChanged", g_variant_new("(@(a{sv}))", CallData(rec)));
    } else if (sig == "CallDeleted") {
        const gchar* cuni = nullptr;
        g_variant_get(params, "(&s)", &cuni);
        if (Calls.erase(cuni ? cuni : "")) {
            Log(std::format("call deleted {}", cuni));
            EmitCalls("callDeleted", g_variant_new("(ss)", DeviceUni, cuni));
        }
    } else if (sig == "RegistrationChanged") {
        gboolean reg = FALSE;
        g_variant_get(params, "(b)", &reg);
        Registered = reg;
        Log(std::format("registration -> {}", Registered));
        EmitDevice("deviceUniListChanged", g_variant_new("(@as)", DeviceList()));
    }
}

void OnImsdAppeared(GDBusConnection*, const gchar*, const gchar*, gpointer) {
    if (ImsdUp) return;
    ImsdUp = true;
    Log("imsd up");
    RefreshStatus();
    EmitDevice("deviceUniListChanged", g_variant_new("(@as)", DeviceList()));
}

void OnImsdVanished(GDBusConnection*, const gchar*, gpointer) {
    if (!ImsdUp) return;
    ImsdUp = false;
    Registered = false;
    Log("imsd down");
    // drop stale calls; tell the UI
    for (auto it = Calls.begin(); it != Calls.end();) {
        it->second.state = StTerminated;
        it->second.reason = RsError;
        EmitCalls("callStateChanged", g_variant_new("(@(a{sv}))", CallData(it->second)));
        EmitCalls("callDeleted", g_variant_new("(ss)", DeviceUni, it->first.c_str()));
        it = Calls.erase(it);
    }
    EmitDevice("deviceUniListChanged", g_variant_new("(@as)", DeviceList()));
}

// ---- session bus objects ----------------------------------------------------
constexpr const char* CallsXml = R"xml(
<node>
  <interface name="org.kde.telephony.CallUtils">
    <method name="formatNumber">
      <arg type="s" name="number" direction="in"/>
      <arg type="s" name="formatted" direction="out"/>
    </method>
    <method name="dial">
      <arg type="s" name="deviceUni" direction="in"/>
      <arg type="s" name="number" direction="in"/>
    </method>
    <method name="accept">
      <arg type="s" name="deviceUni" direction="in"/>
      <arg type="s" name="callUni" direction="in"/>
    </method>
    <method name="hangUp">
      <arg type="s" name="deviceUni" direction="in"/>
      <arg type="s" name="callUni" direction="in"/>
    </method>
    <method name="sendDtmf">
      <arg type="s" name="deviceUni" direction="in"/>
      <arg type="s" name="callUni" direction="in"/>
      <arg type="s" name="tones" direction="in"/>
    </method>
    <method name="fetchCalls"/>
    <method name="setCalls"><arg type="a(a{sv})" name="calls" direction="in"/></method>
    <method name="addCall">
      <arg type="s" name="deviceUni" direction="in"/>
      <arg type="s" name="callUni" direction="in"/>
      <arg type="(i)" name="direction" direction="in"/>
      <arg type="(i)" name="state" direction="in"/>
      <arg type="(i)" name="reason" direction="in"/>
      <arg type="s" name="communicationWith" direction="in"/>
    </method>
    <method name="deleteCall">
      <arg type="s" name="deviceUni" direction="in"/>
      <arg type="s" name="callUni" direction="in"/>
    </method>
    <method name="setCallState"><arg type="(a{sv})" name="callData" direction="in"/></method>
    <signal name="dialed"><arg type="s"/><arg type="s"/></signal>
    <signal name="accepted"><arg type="s"/><arg type="s"/></signal>
    <signal name="hungUp"><arg type="s"/><arg type="s"/></signal>
    <signal name="sentDtmf"><arg type="s"/><arg type="s"/><arg type="s"/></signal>
    <signal name="callsRequested"/>
    <signal name="callsChanged"><arg type="a(a{sv})"/></signal>
    <signal name="callAdded">
      <arg type="s"/><arg type="s"/><arg type="(i)"/><arg type="(i)"/>
      <arg type="(i)"/><arg type="s"/>
    </signal>
    <signal name="callDeleted"><arg type="s"/><arg type="s"/></signal>
    <signal name="callStateChanged"><arg type="(a{sv})"/></signal>
  </interface>
</node>)xml";

constexpr const char* DeviceXml = R"xml(
<node>
  <interface name="org.kde.telephony.DeviceUtils">
    <method name="deviceUniList"><arg type="as" name="list" direction="out"/></method>
    <method name="equipmentIdentifiers"><arg type="as" name="ids" direction="out"/></method>
    <method name="setDeviceUniList"><arg type="as" name="list" direction="in"/></method>
    <method name="setCountryCode"><arg type="s" name="countryCode" direction="in"/></method>
    <signal name="deviceUniListChanged"><arg type="as"/></signal>
    <signal name="countryCodeChanged"><arg type="s"/></signal>
  </interface>
</node>)xml";

constexpr const char* UssdXml = R"xml(
<node>
  <interface name="org.kde.telephony.UssdUtils">
    <method name="initiate">
      <arg type="s" name="deviceUni" direction="in"/>
      <arg type="s" name="command" direction="in"/>
    </method>
    <method name="respond">
      <arg type="s" name="deviceUni" direction="in"/>
      <arg type="s" name="reply" direction="in"/>
    </method>
    <method name="cancel"><arg type="s" name="deviceUni" direction="in"/></method>
    <signal name="initiated"><arg type="s"/><arg type="s"/></signal>
    <signal name="responded"><arg type="s"/><arg type="s"/></signal>
    <signal name="cancelled"><arg type="s"/></signal>
    <signal name="notificationReceived"><arg type="s"/><arg type="s"/></signal>
    <signal name="stateChanged"><arg type="s"/><arg type="s"/></signal>
    <signal name="errorReceived"><arg type="s"/><arg type="s"/></signal>
  </interface>
</node>)xml";

void HandleCallsMethod(GDBusConnection*, const gchar*, const gchar*, const gchar*, const gchar* method, GVariant* params, GDBusMethodInvocation* inv, gpointer) {
    std::string_view m = method;
    if (m == "formatNumber") {
        const gchar* number = nullptr;
        g_variant_get(params, "(&s)", &number);
        g_dbus_method_invocation_return_value(inv, g_variant_new("(s)", number));
        return;
    }
    if (m == "dial") {
        const gchar* dev = nullptr; const gchar* number = nullptr;
        g_variant_get(params, "(&s&s)", &dev, &number);
        Log(std::format("dial({}, {})", dev, number));
        EmitCalls("dialed", g_variant_new("(ss)", dev, number));
        CallImsd("Dial", g_variant_new("(s)", number));
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "accept") {
        const gchar* dev = nullptr; const gchar* uni = nullptr;
        g_variant_get(params, "(&s&s)", &dev, &uni);
        std::string target = (uni && *uni) ? uni : ActiveUni();
        Log(std::format("accept({})", target));
        EmitCalls("accepted", g_variant_new("(ss)", dev, uni));
        CallImsd("Accept", g_variant_new("(s)", target.c_str()));
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "hangUp") {
        const gchar* dev = nullptr; const gchar* uni = nullptr;
        g_variant_get(params, "(&s&s)", &dev, &uni);
        std::string target = (uni && *uni) ? uni : ActiveUni();
        Log(std::format("hangUp({})", target));
        EmitCalls("hungUp", g_variant_new("(ss)", dev, target.c_str()));
        CallImsd("HangUp", g_variant_new("(s)", target.c_str()));
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "sendDtmf") {
        const gchar* dev = nullptr; const gchar* uni = nullptr;
        const gchar* tones = nullptr;
        g_variant_get(params, "(&s&s&s)", &dev, &uni, &tones);
        std::string target = (uni && *uni) ? uni : ActiveUni();
        EmitCalls("sentDtmf", g_variant_new("(sss)", dev, target.c_str(), tones));
        CallImsd("SendDtmf", g_variant_new("(ss)", target.c_str(), tones));
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "fetchCalls") {
        EmitCalls("callsRequested", nullptr);
        EmitCalls("callsChanged", g_variant_new("(@a(a{sv}))", CallVector()));
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    // state-injection methods (interface parity with modem-daemon; the stock
    // daemon's CallManager feeds these — nothing calls them in our setup)
    if (m == "setCalls") {
        EmitCalls("callsChanged", params);
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "addCall") {
        EmitCalls("callAdded", params);
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "deleteCall") {
        EmitCalls("callDeleted", params);
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "setCallState") {
        EmitCalls("callStateChanged", params);
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    g_dbus_method_invocation_return_dbus_error(inv, "org.freedesktop.DBus.Error.UnknownMethod", "no such method");
}

void HandleDeviceMethod(GDBusConnection*, const gchar*, const gchar*, const gchar*, const gchar* method, GVariant* params, GDBusMethodInvocation* inv, gpointer) {
    std::string_view m = method;
    if (m == "deviceUniList") {
        g_dbus_method_invocation_return_value(inv, g_variant_new("(@as)", DeviceList()));
        return;
    }
    if (m == "equipmentIdentifiers") {
        GVariantBuilder b;
        g_variant_builder_init(&b, G_VARIANT_TYPE("as"));
        g_variant_builder_add(&b, "s", "FP6-userspace-IMS");
        g_dbus_method_invocation_return_value(inv, g_variant_new("(as)", &b));
        return;
    }
    if (m == "setDeviceUniList") {
        EmitDevice("deviceUniListChanged", params);
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "setCountryCode") {
        EmitDevice("countryCodeChanged", params);
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    g_dbus_method_invocation_return_dbus_error(inv, "org.freedesktop.DBus.Error.UnknownMethod", "no such method");
}

void HandleUssdMethod(GDBusConnection*, const gchar*, const gchar*, const gchar*, const gchar* method, GVariant* params, GDBusMethodInvocation* inv, gpointer) {
    std::string_view m = method;
    if (m == "initiate") {
        const gchar* dev = nullptr; const gchar* cmd = nullptr;
        g_variant_get(params, "(&s&s)", &dev, &cmd);
        Log(std::format("USSD not supported ({})", cmd));
        EmitUssd("errorReceived", g_variant_new("(ss)", dev, "USSD is not supported on the IMS stack"));
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    if (m == "respond" || m == "cancel") {
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }
    g_dbus_method_invocation_return_dbus_error(inv, "org.freedesktop.DBus.Error.UnknownMethod", "no such method");
}

const GDBusInterfaceVTable CallsVtable = { HandleCallsMethod, nullptr, nullptr, {} };
const GDBusInterfaceVTable DeviceVtable = { HandleDeviceMethod, nullptr, nullptr, {} };
const GDBusInterfaceVTable UssdVtable = { HandleUssdMethod, nullptr, nullptr, {} };

void RegisterObject(GDBusConnection* conn, const char* xml, const char* path, const GDBusInterfaceVTable* vtable) {
    GDBusNodeInfo* node = g_dbus_node_info_new_for_xml(xml, nullptr);
    g_dbus_connection_register_object(conn, path, node->interfaces[0], vtable, nullptr, nullptr, nullptr);
    g_dbus_node_info_unref(node);
}

gboolean OnTerm(gpointer loop) {
    g_main_loop_quit(static_cast<GMainLoop*>(loop));
    return G_SOURCE_REMOVE;
}

}  // namespace

int main() {
    GError* err = nullptr;
    Session = g_bus_get_sync(G_BUS_TYPE_SESSION, nullptr, &err);
    if (!Session) {
        std::println(std::cerr, "imsd-dialerd: no session bus: {}", err ? err->message : "?");
        return 1;
    }
    System = g_bus_get_sync(G_BUS_TYPE_SYSTEM, nullptr, &err);
    if (!System) {
        std::println(std::cerr, "imsd-dialerd: no system bus: {}", err ? err->message : "?");
        return 1;
    }

    RegisterObject(Session, CallsXml, CallsPath, &CallsVtable);
    RegisterObject(Session, DeviceXml, DevicePath, &DeviceVtable);
    RegisterObject(Session, UssdXml, UssdPath, &UssdVtable);

    Loop = g_main_loop_new(nullptr, FALSE);

    // claim the three names the stock modem-daemon owns; DO_NOT_QUEUE so a
    // still-running modem-daemon makes us fail loudly instead of queueing
    for (const char* name : {CallsIface, DeviceIface, UssdIface}) {
        guint id = g_bus_own_name_on_connection(
            Session, name, G_BUS_NAME_OWNER_FLAGS_DO_NOT_QUEUE, nullptr,
            [](GDBusConnection*, const gchar* n, gpointer lp) {
                Log(std::format("lost {} — exiting", n));
                g_main_loop_quit(static_cast<GMainLoop*>(lp));
            },
            Loop, nullptr);
        (void)id;
    }

    g_dbus_connection_signal_subscribe(System, ImsdBus, ImsdIface, nullptr, ImsdPath, nullptr, G_DBUS_SIGNAL_FLAGS_NONE, OnImsdSignal, nullptr, nullptr);
    g_bus_watch_name_on_connection(System, ImsdBus, G_BUS_NAME_WATCHER_FLAGS_NONE, OnImsdAppeared, OnImsdVanished, nullptr, nullptr);

    g_unix_signal_add(SIGTERM, OnTerm, Loop);
    g_unix_signal_add(SIGINT, OnTerm, Loop);

    Log("up (org.kde.telephony.{CallUtils,DeviceUtils,UssdUtils})");
    g_main_loop_run(Loop);
    g_main_loop_unref(Loop);
    return 0;
}
