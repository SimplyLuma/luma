# SPDX-License-Identifier: Apache-2.0
"""Live Weather search source. PlaceSearch calls load/search in kit workers."""
import threading
from .weather_backend import LocationSearch


class WeatherPlaces:
    def __init__(self):
        self.provider = None
        self.lock = threading.Lock()

    def load(self):
        # Even constructing libgweather's world stays off the GTK thread.
        with self.lock:
            if self.provider is None:
                self.provider = LocationSearch()
            self.provider.load()

    def search(self, query):
        if not query.strip():
            return ()
        self.load()
        places = self.provider.search(query)
        if not places and len(query.strip()) >= 2:
            places = self.provider.lookup(query)
        return places
