#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded Photos loading against a catalog-only 100,000-photo fixture.

Never presents a window; visual states use the headless conform server. No real photos, scans or
image decoding: this isolates catalog/query/widget scaling and UI responsiveness.
"""
import os
import resource
import tempfile
import time
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="photos-catalog-test-") as directory:
    root = Path(directory)
    for kind in ("CACHE", "DATA", "CONFIG"):
        os.environ[f"XDG_{kind}_HOME"] = str(root / kind.lower())
    os.environ["GSETTINGS_BACKEND"] = "memory"
    os.environ["LUMA_PHOTOS_NON_UNIQUE"] = "1"
    from prairie_apps import photos as p

    library = p.PhotoLibrary(root / "library.db")
    source = library.add_source(root / "catalog-only", "Test catalog")
    stamp = "2026-09-22T12:00:00+00:00"
    size = 100_000
    with library._connect() as db:
        db.executemany(
            "INSERT INTO assets(id,display_name,media_type,mime_type,modified_at,created_at,width,height) "
            "VALUES(?,?,'image','image/jpeg',?,?,300,512)",
            ((f"asset-{i:06}", f"Picture {i}", stamp, stamp) for i in range(size)),
        )
        db.executemany(
            "INSERT INTO copies(id,asset_id,source_id,uri,bytes,modified_ns,reachable,last_seen) "
            "VALUES(?,?,?,?,100,0,0,?)",
            ((f"copy-{i}", f"asset-{i:06}", source.id,
              (root / f"picture-{i}.jpg").as_uri(), stamp) for i in range(size)),
        )

    class CatalogWindow(p.PhotosWindow):
        def _begin_scan(self):return p.GLib.SOURCE_REMOVE

    app=p.PhotosApplication();app.register(None)
    window=CatalogWindow(app,library=library)
    ticks=[0]
    def heartbeat():
        ticks[0]+=1
        return p.GLib.SOURCE_CONTINUE
    timer=p.GLib.timeout_add(20,heartbeat)
    def settle(predicate):
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            p.GLib.MainContext.default().iteration(False)
            if predicate():return
            time.sleep(.002)
        raise AssertionError('Catalog did not settle')
    def tiles():
        return sum(child.model.get_n_items() for child in children(window.photo_body) if isinstance(child,p.kit.MediaGrid))
    def children(widget):
        child=widget.get_first_child()
        while child:
            yield child
            child=child.get_next_sibling()
    try:
        start=time.monotonic()
        settle(lambda:window.state is not None and window.state.total==size)
        initial_seconds=time.monotonic()-start
        assert not getattr(window,'last_error',''),getattr(window,'last_error','')
        assert len(window.state.records)==p.PAGE_SIZE
        assert tiles()==p.PAGE_SIZE
        assert ticks[0]>0,'Loading did not yield to the main loop'
        assert not window.get_visible()
        assert any(child.get_name()=='ph-page-navigation' for child in children(window.photo_body)), \
            'A large collection must expose its older photos'
        window._change_page(1)
        settle(lambda:window.state.records[0].id=='asset-000200')
        assert tiles()==p.PAGE_SIZE
        assert library.page_offset_for_path(root/'picture-99999.jpg')==99800
        start=time.monotonic()
        window._search_changed('Picture 99999')
        settle(lambda:window.state.total==1)
        search_seconds=time.monotonic()-start
        assert window.state.records[0].id=='asset-099999'
        assert tiles()==1
        window._search_changed('Picture')
        window._search_changed('Picture 99998')
        settle(lambda:window.state.records and window.state.records[0].id=='asset-099998')
        assert window.state.total==1
        print(f'PASS: {size:,} catalog entries; at most {p.PAGE_SIZE} tiles; '
              f'initial {initial_seconds:.2f}s; search {search_seconds:.2f}s; '
              f'UI ticks {ticks[0]}; peak RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.1f} MiB')
    finally:
        p.GLib.source_remove(timer)
        window._close_requested();window.destroy()
