// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file fixed-width-types no-char-pointer
/*
ImsdBus — what the two system-bus daemons (imsd, imsd-ofonod) share on the
bus side: the polkit gate in front of call control.

The bus policies let any local account reach both names, so the session
account can dial whatever it is called; whether a caller may place, answer
or end a call is polkit's net.catcrafts.IMS1.Voice
(packaging/net.catcrafts.IMS1.policy), shaped like ModemManager's Voice
action: the active local session and root, not an ssh login, an inactive
session or a system service account. Reads (status, the call list) stay
open, as ModemManager's are to every local account; the call signals reach
them all anyway.

GLib, so not part of the GLib-free imsd-core.
*/

module;
#include <gio/gio.h>
export module ImsdBus;
import std;

namespace imsd::bus {

constexpr const char* VoiceAction = "net.catcrafts.IMS1.Voice";

// CheckAuthorization's AllowUserInteraction, as ModemManager passes it: a
// rule an administrator sets to auth_self prompts through the session's
// polkit agent.
constexpr guint32 AllowUserInteraction = 1;

struct Pending {
    GDBusMethodInvocation* inv;
    GDBusInterfaceMethodCallFunc body;
    const char* deniedError;
    void (*log)(std::string_view);
};

void OnCheck(GObject* src, GAsyncResult* res, gpointer data) {
    std::unique_ptr<Pending> p(static_cast<Pending*>(data));
    GDBusMethodInvocation* inv = p->inv;
    GError* err = nullptr;
    GVariant* r = g_dbus_connection_call_finish(G_DBUS_CONNECTION(src), res, &err);
    gboolean authorized = FALSE;
    gboolean challenge = FALSE;
    if (r) {
        GVariant* details = nullptr;
        g_variant_get(r, "((bb@a{ss}))", &authorized, &challenge, &details);
        g_variant_unref(details);
        g_variant_unref(r);
    }
    if (authorized) {
        p->body(g_dbus_method_invocation_get_connection(inv), g_dbus_method_invocation_get_sender(inv),
                g_dbus_method_invocation_get_object_path(inv), g_dbus_method_invocation_get_interface_name(inv),
                g_dbus_method_invocation_get_method_name(inv), g_dbus_method_invocation_get_parameters(inv), inv,
                g_dbus_method_invocation_get_user_data(inv));
        return;
    }
    std::string why = err ? std::format("{}: polkit did not answer: {}", g_dbus_method_invocation_get_sender(inv), err->message)
                          : std::format("{} is not authorized for {}", g_dbus_method_invocation_get_sender(inv), VoiceAction);
    p->log(std::format("{} refused: {}", g_dbus_method_invocation_get_method_name(inv), why));
    g_dbus_method_invocation_return_dbus_error(inv, p->deniedError, why.c_str());
    if (err) g_error_free(err);
}

}  // namespace imsd::bus

export namespace imsd::bus {

// Off for a --session run: the session bus has no polkit, and its callers
// are the owner's own processes.
inline bool AuthorizeCalls = true;

// Run `body` for the call `inv` once polkit allows its sender
// net.catcrafts.IMS1.Voice; otherwise answer `deniedError` and log why. The
// check is asynchronous — nothing else on the main loop waits for it.
void Authorize(GDBusMethodInvocation* inv, GDBusInterfaceMethodCallFunc body, const char* deniedError, void (*log)(std::string_view)) {
    GVariantBuilder subject;
    g_variant_builder_init(&subject, G_VARIANT_TYPE("a{sv}"));
    g_variant_builder_add(&subject, "{sv}", "name", g_variant_new_string(g_dbus_method_invocation_get_sender(inv)));
    GVariantBuilder details;
    g_variant_builder_init(&details, G_VARIANT_TYPE("a{ss}"));
    g_dbus_connection_call(g_dbus_method_invocation_get_connection(inv), "org.freedesktop.PolicyKit1",
                           "/org/freedesktop/PolicyKit1/Authority", "org.freedesktop.PolicyKit1.Authority",
                           "CheckAuthorization",
                           g_variant_new("((sa{sv})sa{ss}us)", "system-bus-name", &subject, VoiceAction, &details, AllowUserInteraction, ""),
                           G_VARIANT_TYPE("((bba{ss}))"), G_DBUS_CALL_FLAGS_NONE, G_MAXINT, nullptr, OnCheck,
                           new Pending{ inv, body, deniedError, log });
}

// A vtable method handler for `Body` that lets `IsRead` methods through and
// puts every other method — call control, and anything added later — behind
// Authorize.
template <GDBusInterfaceMethodCallFunc Body, bool (*IsRead)(std::string_view), const char* DeniedError, void (*Log)(std::string_view)>
void Gated(GDBusConnection* conn, const gchar* sender, const gchar* path, const gchar* iface, const gchar* method, GVariant* params, GDBusMethodInvocation* inv, gpointer user) {
    if (!AuthorizeCalls || IsRead(method)) {
        Body(conn, sender, path, iface, method, params, inv, user);
        return;
    }
    Authorize(inv, Body, DeniedError, Log);
}

}  // namespace imsd::bus
