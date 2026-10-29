# SPDX-License-Identifier: Apache-2.0
"""Real private bus/GTK/loopback OAuth; provider replies are deterministic.

Set ARI_CHATGPT_PRIVATE_TEST=1 inside dbus-run-session with isolated XDG dirs.
ARI_TEST_KEYRING_DAEMON optionally selects a privately extracted real daemon;
otherwise credentials use the in-memory test double. No OpenAI traffic occurs.
"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from unittest.mock import patch


def daemon():
    from ari import chatgpt, cloud, paths, service
    from test_chatgpt import MemoryKeyring, TestTransport, DISCOVERY
    from test_chatgpt_provider import stream, completed
    service.TOOL_SERVERS = ("answers",)
    if os.environ.get("ARI_TEST_INSTALLED_SHARE"):
        paths.share_dir = lambda: Path(os.environ["ARI_TEST_INSTALLED_SHARE"])
    cloud.load_key = lambda: ""
    cloud.catalogue = lambda **_kwargs: []
    d = service.Daemon()
    transport = TestTransport()
    original_json, original_open = transport.json, transport.open
    def read(url, **kwargs):
        if url == DISCOVERY["token_endpoint"] and kwargs.get("form", {}).get("grant_type") == "authorization_code":
            transport.nonce = d.chatgpt.login.nonce
        result = original_json(url, **kwargs)
        if url == chatgpt.API + "/models":
            result["models"][0].update(supported_reasoning_levels=[{"effort":"low"}, {"effort":"high"}], default_reasoning_level="low")
        return result
    def opened(url, **kwargs):
        if url == chatgpt.API + "/responses":
            body = kwargs["body"]
            assert {"type":"web_search"} in body["tools"]
            assert body.get("reasoning") == {"effort":"high"}
            assert "does not give you access to ChatGPT chats or memory" in body["instructions"]
            if os.environ.get("ARI_CHATGPT_CAPTURE"):
                Path(os.environ["ARI_CHATGPT_CAPTURE"], "request-capabilities.json").write_text(json.dumps({
                    "fixture_only":True, "hosted_search":True, "reasoning":body["reasoning"],
                    "persona_reaches_instructions":True, "store":body["store"], "stream":body["stream"]}))
            inputs = body["input"]
            if any("limit" in str(item.get("content", "")) for item in inputs if item.get("role") == "user"):
                return stream({"type": "response.output_text.delta", "delta": "Unfinished test reply"},
                              {"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}})
            return stream({"type": "response.output_text.delta", "delta": "Hello from the test model."}, completed([
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Hello from the test model."}]}]))
        return original_open(url, **kwargs)
    transport.json, transport.open = read, opened
    d.chatgpt = chatgpt.Manager(transport=transport,
                              keyring=chatgpt.Keyring() if os.environ.get("ARI_TEST_KEYRING_DAEMON") else MemoryKeyring())
    d.run()


def main():
    if os.environ.get("ARI_CHATGPT_PRIVATE_TEST") != "1" or os.getuid() == 0:
        raise RuntimeError("Use an explicitly isolated private-bus fixture as a development user")
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gio, GLib, Gtk
    from ari_ui.lumaui_app import AriApplication, AriWindow

    def settle(predicate, seconds=12):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError("Ari did not reach the expected ChatGPT state")

    def descendants(widget):
        yield widget
        child = widget.get_first_child()
        while child:
            yield from descendants(child)
            child = child.get_next_sibling()

    def find(widget, name):
        return next(item for item in descendants(widget) if item.get_name() == "ari-chatgpt-" + name)

    def capture(name):
        target = os.environ.get("ARI_CHATGPT_CAPTURE")
        if target:
            end = time.monotonic() + .2
            while time.monotonic() < end:
                while GLib.MainContext.default().pending():
                    GLib.MainContext.default().iteration(False)
                time.sleep(.005)
            from PIL import ImageGrab
            ImageGrab.grab().save(Path(target) / (name + ".png"))

    children = []
    if os.environ.get("ARI_TEST_KEYRING_DAEMON"):
        keyring = subprocess.Popen([os.environ["ARI_TEST_KEYRING_DAEMON"], "--foreground", "--components=secrets", "--unlock"],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        keyring.stdin.write(b"Ari private test keyring password\n")
        keyring.stdin.close()
        children.append(keyring)
        # GetNameOwner waits for the actual test service, without accessing a
        # login keyring or depending on desktop autostart configuration.
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        def owns_secrets():
            try:
                bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetNameOwner",
                              GLib.Variant("(s)", ("org.freedesktop.secrets",)), None, Gio.DBusCallFlags.NONE, 1000, None)
                return True
            except GLib.Error:
                return False
        settle(owns_secrets)
    service_process = subprocess.Popen([sys.executable, __file__, "--daemon"])
    children.append(service_process)
    app = AriApplication()
    assert app.register(None)
    window = AriWindow(app)
    window.show_permissions()  # A cold-start settings request waits for D-Bus.
    if os.environ.get("ARI_TEST_INSTALLED_SHARE"):
        Gtk.IconTheme.get_for_display(window.get_display()).add_search_path(
            str(Path(os.environ["ARI_TEST_INSTALLED_SHARE"]).parent / "icons"))
    window.present()
    settle(lambda:window.permissions_dialog is not None)
    window.permissions_dialog.close()
    settle(lambda:window.permissions_dialog is None)
    launches = []
    complete_browser = [True]
    def browser(url, _context):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        launches.append(query)
        assert urllib.parse.urlsplit(url).netloc == "auth.openai.com"
        if complete_browser[0]:
            callback = query["redirect_uri"][0] + "?" + urllib.parse.urlencode({
                "state": query["state"][0], "code": "fixture-code", "client_id": "oaiapp_fixture"})
            with urllib.request.urlopen(callback, timeout=10) as response:
                assert b"Return to Ari" in response.read()
        return True
    try:
        settle(lambda: any(p["id"] == "chatgpt" for p in window.data["providers"]))
        window.show_chatgpt()
        dialog = window.chatgpt_dialog
        settle(lambda: not dialog.working)
        assert not dialog.data.get("error"), dialog.data.get("error")
        capture("chatgpt-before-signin")
        with patch.object(Gio.AppInfo, "launch_default_for_uri", side_effect=browser):
            find(dialog, "signin").emit("clicked")
            settle(lambda: bool(dialog.data.get("models")) and not dialog.working)
        assert launches[0]["client_id"] == ["dynamic_agent_client"]
        assert dialog.data["accounts"][0]["plan_enabled"]
        profile = dialog.data["active"]
        assert "test-access" not in json.dumps(dialog.data)
        assert not any("test-access" in path.read_text(errors="ignore") for path in Path(os.environ["XDG_CONFIG_HOME"]).rglob("*.json"))
        for width in (360, 1180):
            window.set_default_size(width, 740)
            settle(lambda: window.get_surface().get_width() == width)
            settle(lambda: find(dialog, "use").get_mapped())
            assert find(dialog, "use").get_width() > 100
            assert any(isinstance(item, Gtk.LinkButton) and item.get_uri() == "https://chatgpt.com/settings/usage"
                       for item in descendants(dialog))
            capture("chatgpt-connected-" + str(width))
        find(dialog, "use").emit("clicked")
        settle(lambda: window.state["model"] == "chatgpt:test-model" and window.chatgpt_dialog is None)
        assert "ChatGPT" in window.model_button.get_tooltip_text()
        assert not any(isinstance(item, Gtk.Label) and "Using ChatGPT plan" in item.get_text() for item in descendants(window.bar))
        assert window.effort_options("chatgpt:test-model")["levels"] == ["low", "high"]
        window.set_effort("chatgpt:test-model", "high")
        settle(lambda: window.chatgpt_data.get("efforts", {}).get("test-model") == "high")
        permission_result=[]
        window.daemon.call('SetActionPermission','(ss)',('enter_text@weather','allow'),permission_result.append)
        settle(lambda:bool(permission_result))
        assert permission_result[0] == (True,),permission_result
        window.show_permissions()
        settle(lambda:any(item.get_name()=='advanced-permissions' and item.get_mapped()
                          for item in descendants(window.permissions_dialog)))
        capture('permissions-autonomy')
        window.permissions_dialog.close()
        settle(lambda:window.permissions_dialog is None)
        previous_status = window.chatgpt_data
        service_process.terminate()
        service_process.wait(timeout=5)
        settle(lambda: not window.daemon.proxy.get_name_owner())
        service_process = subprocess.Popen([sys.executable, __file__, "--daemon"])
        children.append(service_process)
        settle(lambda: window.daemon.proxy.get_name_owner() and window.chatgpt_data is not previous_status)
        assert window.chatgpt_data["active"] == profile
        assert window.state["model"] == "chatgpt:test-model"
        assert window.chatgpt_data["efforts"]["test-model"] == "high"
        permission_status=[]
        window.daemon.call('Settings',None,(),permission_status.append)
        settle(lambda:bool(permission_status))
        assert json.loads(permission_status[0][0])['action_permissions']['enter_text@weather']=='allow'
        window.send("Tell me a short story about clouds")
        settle(lambda: not window.state["busy"])
        conversation = next(item for item in window.data["conversations"] if item["id"] == window.state["cur"])
        assert any(item.get("a") == "Hello from the test model." for item in conversation["msgs"])
        window.send("Explain a usage limit")
        settle(lambda: not window.state["busy"])
        assert "Manage usage" in conversation["msgs"][-1].get("a", "")
        assert not any("Unfinished" in item.get("a", "") for item in conversation["msgs"])
        window.show_chatgpt()
        dialog = window.chatgpt_dialog
        settle(lambda: not dialog.working)
        find(dialog, "signout").emit("clicked")
        settle(lambda: not dialog.working and not dialog.data["accounts"][0]["signed_in"])
        assert dialog.data["active"] == profile
        assert dialog.data["models"] == []
        complete_browser[0] = False
        with patch.object(Gio.AppInfo, "launch_default_for_uri", side_effect=browser):
            find(dialog, "signin").emit("clicked")
            settle(lambda: bool(dialog.attempt))
        assert launches[-1]["client_id"] == ["oaiapp_fixture"]
        assert launches[-1]["ext_agent_host_id"] == launches[0]["ext_agent_host_id"]
        find(dialog, "cancel").emit("clicked")
        assert not dialog.attempt
        dialog.close()
        mode = "real Secret Service" if os.environ.get("ARI_TEST_KEYRING_DAEMON") else "in-memory credential fixture"
        print("ARI CHATGPT: private D-Bus, browser callback, signed identity, " + mode +
              ", model selection, effort persistence/request, hosted search request, persona, remembered permissions/UI and daemon restart, completed inference, late limit failure, signout, returning login and cancellation PASS", flush=True)
    finally:
        window.close()
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        daemon()
    else:
        main()
