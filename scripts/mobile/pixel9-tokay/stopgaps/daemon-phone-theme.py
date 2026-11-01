# Pixel 9 stopgap: prairie_apps.phone no longer exports install_phone_theme
# (fix on work/mobile-data-quick-toggle f8d7f594); define it in the daemon.
F = "/var/lib/luma-tokay/prairie-phone-daemon"
s = open(F).read()
old = "from prairie_apps.phone import install_phone_theme  # noqa: E402\n"
new = '''import prairie_apps.phone as _phone  # noqa: E402


def install_phone_theme() -> None:
    if hasattr(_phone, "install_phone_theme"):
        return _phone.install_phone_theme()
    import os
    from pathlib import Path
    path = os.environ.get("LUMA_PHONE_STYLE_PATH", str(Path(_phone.__file__).parents[1] / "style/phone.css"))
    if not Path(path).is_file():
        path = "/usr/share/prairie-core/phone.css"
    _phone.add_style_sheet(path)


'''
assert s.count(old) == 1
open(F, "w").write(s.replace(old, new))
print("patched theme")
