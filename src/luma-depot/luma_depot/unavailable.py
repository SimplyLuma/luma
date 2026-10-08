# SPDX-License-Identifier: Apache-2.0
"""Truthful provider answers until an application service is configured."""
from .providers import CatalogProvider, InstallationProvider, Catalogue, ProviderError, run_async


class UnconfiguredCatalogue(CatalogProvider):
    def load_catalogue(self, callback, cancellable=None):
        run_async(lambda: Catalogue((), ()), callback, cancellable)

    def search(self, query, callback, cancellable=None):
        run_async(lambda: (), callback, cancellable)

    def app(self, app_id, callback, cancellable=None):
        run_async(_unavailable, callback, cancellable)


def _unavailable():
    raise ProviderError("No application service", hint="An application service has not been configured.")


class UnconfiguredInstallation(InstallationProvider):
    def installed(self, callback, cancellable=None):
        run_async(lambda: (), callback, cancellable)

    def free_bytes(self):
        return 0

    def install(self, app, on_progress, callback, cancellable=None):
        run_async(_unavailable, callback, cancellable)

    def update(self, app_id, on_progress, callback, cancellable=None, *, expected_commit="", expected_installed_commit=""):
        run_async(_unavailable, callback, cancellable)

    def remove(self, app_id, *, keep_data, callback, cancellable=None):
        run_async(_unavailable, callback, cancellable)
