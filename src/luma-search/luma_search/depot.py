# SPDX-License-Identifier: Apache-2.0
"""Read-only Depot catalogue search using the existing catalogue trust policy."""
from urllib.parse import quote
from . import ranking
from .providers import desktop_app_info

PATH = '/org/projectluma/Search/Depot'
INTERFACE = 'org.gnome.Shell.SearchProvider2'
XML = '''<node><interface name="org.gnome.Shell.SearchProvider2">
<method name="GetInitialResultSet"><arg type="as" direction="in"/><arg type="as" direction="out"/></method>
<method name="GetSubsearchResultSet"><arg type="as" direction="in"/><arg type="as" direction="in"/><arg type="as" direction="out"/></method>
<method name="GetResultMetas"><arg type="as" direction="in"/><arg type="aa{sv}" direction="out"/></method>
<method name="ActivateResult"><arg type="s" direction="in"/><arg type="as" direction="in"/><arg type="u" direction="in"/></method>
<method name="LaunchSearch"><arg type="as" direction="in"/><arg type="u" direction="in"/></method>
</interface></node>'''


def entries():
    from luma_installer.depot_catalog import CatalogError, local_catalog
    try:
        return tuple(item for item in local_catalog().applications if item.visibility == 'public')
    except CatalogError:
        return ()


def matches(catalogue, terms, installed=()):
    terms = ranking.split_terms(' '.join(terms))
    if not terms:
        return ()
    installed = set(installed)
    ranked = []
    for item in catalogue:
        if item.visibility != 'public' or item.identifier + '.desktop' in installed:
            continue
        score = ranking.score_app(item.name, terms, generic_name=item.summary,
                                  keywords=item.categories)
        if score is not None:
            ranked.append((score, item.name.casefold(), item.id))
    return tuple(item[2] for item in sorted(ranked, key=lambda item: (-item[0], item[1], item[2]))[:6])


class Provider:
    def __init__(self, pool, touch):
        from gi.repository import Gio
        self.pool, self.touch = pool, touch
        self.node = Gio.DBusNodeInfo.new_for_xml(XML)

    def register(self, connection):
        connection.register_object(PATH, self.node.interfaces[0], self.call, None, None)

    def call(self, connection, sender, path, interface, method, parameters, invocation):
        from gi.repository import GLib
        self.touch()
        future = self.pool.submit(self.execute, method, parameters.unpack())
        future.add_done_callback(lambda completed: GLib.idle_add(self.finish, invocation, completed))

    @staticmethod
    def finish(invocation, completed):
        try:
            invocation.return_value(completed.result())
        except Exception as error:
            invocation.return_dbus_error('org.projectluma.Search.Error.Depot', str(error))
        return False

    @staticmethod
    def execute(method, arguments):
        from gi.repository import Gio, GLib
        catalogue = entries()
        if method in ('GetInitialResultSet', 'GetSubsearchResultSet'):
            installed = (app.get_id() for app in Gio.AppInfo.get_all())
            return GLib.Variant('(as)', (matches(catalogue, arguments[-1], installed),))
        by_id = {item.id: item for item in catalogue}
        if method == 'GetResultMetas':
            result = []
            for identifier in arguments[0][:6]:
                item = by_id.get(identifier)
                if item is None:
                    continue
                result.append({key: GLib.Variant('s', value) for key, value in {
                    'id': item.id, 'name': item.name,
                    'description': item.summary or 'Available in Depot',
                    'gicon': 'org.projectluma.Depot', 'lumaKindLabel': 'Depot',
                }.items()})
            return GLib.Variant('(aa{sv})', (result,))
        if method not in ('ActivateResult', 'LaunchSearch'):
            raise ValueError('Unknown Depot search method')
        app = desktop_app_info('org.projectluma.Depot.desktop')
        if app is None:
            raise ValueError('Depot is unavailable')
        if method == 'ActivateResult':
            identifier = arguments[0]
            if identifier not in by_id:
                raise ValueError('Catalogue result is unavailable')
            app.launch_uris(['luma-depot://app/' + quote(identifier, safe='')], None)
        else:
            app.launch([], None)
        return GLib.Variant('()', ())
