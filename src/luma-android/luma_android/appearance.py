"""Read the host appearance authorities used by Luma Shell State."""

VALID_TREATMENTS = {"light", "dark", "frost", "glass"}


def _settings(schema_id, key):
    try:
        from gi.repository import Gio
    except ImportError:
        return None
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(schema_id, True) if source else None
    if schema is None or not schema.has_key(key):
        return None
    return Gio.Settings.new_full(schema, None, None)


def settings():
    return _settings("org.gnome.desktop.interface", "color-scheme")


def treatment_settings():
    return _settings("org.project_luma.shell-state", "surface-treatment")


def accessibility_settings():
    return _settings("org.gnome.desktop.a11y.interface", "high-contrast")


def color_scheme():
    interface = settings()
    return "dark" if interface and interface.get_string("color-scheme") == "prefer-dark" else "light"


def surface_treatment():
    treatment = treatment_settings()
    selected = treatment.get_user_value("surface-treatment") if treatment else None
    requested = treatment.get_string("surface-treatment") if selected is not None else color_scheme()
    requested = requested if requested in VALID_TREATMENTS else color_scheme()
    accessibility = accessibility_settings()
    if accessibility and accessibility.get_boolean("high-contrast"):
        return color_scheme()
    if (requested in {"frost", "glass"} and treatment and
            treatment.get_boolean("reduce-transparency")):
        return color_scheme()
    return requested
