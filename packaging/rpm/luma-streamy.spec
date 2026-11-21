# SPDX-License-Identifier: Apache-2.0
# Streamy: live streaming and recording on LumaUI, with its lumaframe
# GStreamer element (gst-lumaframe, libgstlumavideofx.so) that draws each
# picture widget's rounded corners, stroke and shadow on the GPU.
%global debug_package %{nil}

Name:           luma-streamy
Version:        0.1.0
Release:        1.luma.8%{?dist}
Summary:        Stream and record your screen, windows and camera
# The app: Apache-2.0. gst-lumaframe: MPL-2.0. Vendored crates (Cargo.lock):
# gstreamer-rs and glib-rs (MIT OR Apache-2.0, MIT), memchr (Unlicense OR
# MIT), target-lexicon (Apache-2.0 WITH LLVM-exception), unicode-ident
# ((MIT OR Apache-2.0) AND Unicode-3.0).
License:        Apache-2.0 AND MPL-2.0 AND MIT AND (MIT OR Apache-2.0) AND (Unlicense OR MIT) AND Apache-2.0 WITH LLVM-exception AND Unicode-3.0
URL:            https://simplyluma.com/
ExclusiveArch:  x86_64 aarch64
# scripts/packages/build-luma-streamy-source.sh: the app plus the plugin with
# every crate vendored against Cargo.lock, so the build needs no network.
Source0:        luma-streamy.tar.gz

BuildRequires:  cargo >= 1.92
BuildRequires:  rust >= 1.92
BuildRequires:  gcc
BuildRequires:  pkgconf-pkg-config
BuildRequires:  gstreamer1-devel
BuildRequires:  gstreamer1-plugins-base-devel
BuildRequires:  python3-devel
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib
# %%check runs the plugin's GL pipelines on llvmpipe, a recording engine with
# drawn stand-in sources, and opens the window under a private X server.
BuildRequires:  gstreamer1-plugins-base-tools
BuildRequires:  gstreamer1-plugins-good
BuildRequires:  gstreamer1-plugins-bad-free
BuildRequires:  gstreamer1-plugin-gtk4
BuildRequires:  mesa-dri-drivers
BuildRequires:  mesa-libEGL
BuildRequires:  python3-gobject
BuildRequires:  python3-cairo
BuildRequires:  gtk4 >= 4.22
BuildRequires:  libadwaita >= 1.9
BuildRequires:  luma-developer-platform >= 0.1.0-1.luma.143
BuildRequires:  xorg-x11-server-Xvfb
BuildRequires:  dbus-daemon

Requires:       python3
Requires:       python3-gobject
Requires:       gtk4 >= 4.22
Requires:       libadwaita >= 1.9
# Dock (fitted, movable seams), NavigationList, ArrangeView with crop, ColorPicker and the AudioMeter.
Requires:       luma-developer-platform >= 0.1.0-1.luma.143
Requires:       google-figtree-fonts
# The program graph: GL upload, mixing and conversion (base), FLV/MKV/MP4
# muxers, PulseAudio capture and level meters (good), RTMP(S), VA-API
# encoders, parsers and AAC (bad-free), the preview sink (gtk4), and portal
# capture of displays, windows and cameras (pipewire).
Requires:       gstreamer1
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good
Requires:       gstreamer1-plugins-bad-free
Requires:       gstreamer1-plugin-gtk4
Requires:       pipewire-gstreamer
# Software H.264 for computers without a hardware encoder.
Requires:       gstreamer1-plugin-openh264
Requires:       xdg-desktop-portal

%description
Streamy puts displays, windows, cameras, images and sound together in scenes
and sends them live to an RTMP(S) server or records them to MKV or MP4. Every
picture widget can have rounded corners, a stroke and a soft shadow, drawn on
the graphics card by the lumaframe GStreamer element this package includes.
Only the scene on screen captures, and nothing runs while it isn't in use.

%prep
%autosetup -n luma-streamy

%build
cd gst-lumaframe
export CARGO_HOME=$PWD/.cargo-home
cargo build --release --offline --locked -j %{_smp_build_ncpus}

%install
install -d %{buildroot}%{python3_sitelib}/luma_streamy
install -m 0644 luma_streamy/*.py %{buildroot}%{python3_sitelib}/luma_streamy/
install -D -m 0755 bin/streamy %{buildroot}%{_bindir}/streamy
install -D -m 0644 style/streamy.css %{buildroot}%{_datadir}/luma-streamy/streamy.css
install -D -m 0644 data/org.projectluma.Streamy.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Streamy.desktop
install -D -m 0644 data/org.projectluma.Streamy.metainfo.xml \
  %{buildroot}%{_metainfodir}/org.projectluma.Streamy.metainfo.xml
install -D -m 0644 data/org.projectluma.Streamy.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Streamy.svg
install -D -m 0755 gst-lumaframe/target/release/libgstlumavideofx.so \
  %{buildroot}%{_libdir}/gstreamer-1.0/libgstlumavideofx.so
# Licence texts of every vendored crate, by crate.
(cd gst-lumaframe/vendor && find . -mindepth 2 -maxdepth 2 -type f \( -iname 'LICENSE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' \) -print0 |
  while IFS= read -r -d '' file; do install -D -m 0644 "$file" "../../licenses/vendor/$file"; done)
install -D -m 0644 gst-lumaframe/LICENSE licenses/gst-lumaframe/LICENSE
found=$(find licenses/vendor -type f | wc -l)
[ "$found" -gt 20 ] || { echo "only $found vendored licence files were found" >&2; exit 1; }

%check
python3 -m py_compile %{buildroot}%{python3_sitelib}/luma_streamy/*.py
desktop-file-validate %{buildroot}%{_datadir}/applications/org.projectluma.Streamy.desktop
appstream-util validate-relax --nonet %{buildroot}%{_metainfodir}/org.projectluma.Streamy.metainfo.xml
# Scene model, layouts and the pipeline graph.
PYTHONPATH=. python3 -B -m unittest discover -s tests -p 'test_*.py' -v
# Headless GL: Mesa's EGL surfaceless platform on llvmpipe.
export GST_GL_PLATFORM=egl GST_GL_WINDOW=surfaceless LIBGL_ALWAYS_SOFTWARE=1
(
  cd gst-lumaframe
  export CARGO_HOME=$PWD/.cargo-home
  # Geometry and shadow maths, then real GL pipelines through the element.
  cargo test --offline --locked -j %{_smp_build_ncpus} --lib
  cargo test --offline --locked -j %{_smp_build_ncpus} --test pipeline -- --test-threads=2
)
export GST_PLUGIN_PATH=%{buildroot}%{_libdir}/gstreamer-1.0 GST_REGISTRY=$PWD/gst-registry.bin
# The installed plugin registers the element.
gst-inspect-1.0 lumaframe | grep -q 'libgstlumavideofx.so'
# A failing or silent source comes out of the program without stalling it.
PYTHONPATH=%{buildroot}%{python3_sitelib} timeout 120 python3 -B tests/engine_failure.py
# A microphone meters at once, also beside a source that failed; a frameless widget draws its Look.
PYTHONPATH=%{buildroot}%{python3_sitelib} timeout 120 python3 -B tests/engine_levels.py
PYTHONPATH=%{buildroot}%{python3_sitelib} timeout 120 python3 -B tests/engine_placeholder.py
# The installed app opens on drawn stand-in sources and renders its window
# at phone and desktop widths. The first try waits 15 s: on a loaded builder
# the window can take that long to map while llvmpipe composites the scene.
mkdir -p check-home
# Every editor key and menu reaches a method; switching scenes or a relayout never moves a panel.
HOME=$PWD/check-home XDG_DATA_HOME=$PWD/check-home/data XDG_CONFIG_HOME=$PWD/check-home/config \
  XDG_CACHE_HOME=$PWD/check-home/cache PYTHONPATH=%{buildroot}%{python3_sitelib} \
  timeout 150 xvfb-run -a -s "-screen 0 1920x1080x24" dbus-run-session -- \
  env GDK_BACKEND=x11 GSK_RENDERER=cairo python3 -B tests/window_actions.py
for width in 360 1280; do
  HOME=$PWD/check-home XDG_DATA_HOME=$PWD/check-home/data XDG_CONFIG_HOME=$PWD/check-home/config \
    XDG_CACHE_HOME=$PWD/check-home/cache STREAMY_FAKE_SOURCES=1 STREAMY_SHOT_DELAY=15000 STREAMY_SHOT_TRIES=120 \
    STREAMY_SHOT=$PWD/shot-$width.png STREAMY_SHOT_WIDTH=$width \
    PYTHONPATH=%{buildroot}%{python3_sitelib} \
    timeout 240 xvfb-run -a -s "-screen 0 1920x1080x24" dbus-run-session -- \
    env GDK_BACKEND=x11 GSK_RENDERER=cairo python3 -B %{buildroot}%{_bindir}/streamy
  test -s shot-$width.png
done

%post
update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :
gtk4-update-icon-cache --force --quiet %{_datadir}/icons/hicolor >/dev/null 2>&1 || :

%postun
update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :
gtk4-update-icon-cache --force --quiet %{_datadir}/icons/hicolor >/dev/null 2>&1 || :

%files
%license LICENSE.md licenses
%doc README.md
%{python3_sitelib}/luma_streamy/
%{_bindir}/streamy
%{_datadir}/luma-streamy/
%{_datadir}/applications/org.projectluma.Streamy.desktop
%{_metainfodir}/org.projectluma.Streamy.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Streamy.svg
%{_libdir}/gstreamer-1.0/libgstlumavideofx.so

%changelog
* Fri Nov 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8
- lumaframe: a revision property commits property changes together and
  stamps output buffers; downscaled content is box-filtered over each output
  pixel's footprint
- Mixer pads take the geometry of the buffer being composited, so Look
  changes never move the picture; window casts prefer alpha formats
- Inspector beside the island (Source, Look, Arrange; the scene with nothing
  selected); menus at the pointer with Order/Size/Align/Look submenus; inline
  rename in rows; per-field Look overrides with a sync key; panel moves undo
- Landscape and Vertical are separate preview panels with canvas chips; each
  source is captured once and teed to one compositor per canvas, which runs
  only while its panel shows or an output takes it; edits go to the canvas
  clicked
- Window casts negotiate alpha formats only, opaque when none is offered
- Every camera on the portal is listed (V4L2 and libcamera, one row each);
  sound devices get short names; monitoring is one "Hear it on" choice
- Stills and placeholder cards are uploaded once; per-frame layer reports
  run only while something changes

* Fri Nov 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7
- lumaframe: content-x/-y/-width/-height pick the source region before
  crop-*; with content-width 0 a buffer's GstVideoCropMeta does, so a window
  cast inside a larger buffer fills the box

* Fri Nov 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Source first in every widget's bar: cameras by name with resolution, frame
  rate and mirror; microphones and outputs; Change… for shares; the pointer
- Failures name their cause; a window cast that sends nothing yet waits
- The program runs on past a source that fails, so meters and recordings do
- Look: presets, one-column sliders, border colour picker and position
- Arrange: position, crop, order, opacity; canvas crop and size commands
- Widgets inset beside Audio; Scenes-only sidebar with its + heading
- Undo and redo for every edit; per-canvas placements linked to the primary
- Placeholder cards for widgets without a picture; monitoring for sound

* Fri Nov 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- lumaframe: stroke-position (inside, center, outside); an outside or centred
  stroke grows past the box with concentric corners and the shadow starts
  beyond it

* Fri Nov 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Audio strips use LumaUI's VolumeSlider: the stereo meter lies along the
  fader's track, 0 dB is marked, muted goes quiet

* Fri Nov 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- App audio widgets through luma_appkit.audio_capture (the Share sound
  picker, restore tokens); a capture lives while its scene is shown.
- A refused or failed restore token falls back to the picker once; a
  cancelled, timed-out or failed share leaves the widget saying so with
  its fix in the bar and the Source editor.
- Registers its app id with xdg-desktop-portal, so the picker and the
  sharing pill name Streamy and keep its restore tokens.
- The Audio inset is as tall as its strips; on a phone it stays under the
  preview. Widget rows end in their eye or speaker; hidden and muted show.
- An open editor keeps the selection's name in the bar.
- SVT-AV1 (svtav1enc) as the software AV1 encoder after VA AV1:
  preset 10, bitrate-targeted, a key frame every 2 s.
* Fri Nov 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First package: scenes of displays, windows, cameras, images and sound;
  rounded corners, a stroke and a shadow per picture through lumaframe;
  streaming to RTMP(S) and recording to MKV or MP4.
