# Luma copy of https://src.fedoraproject.org/rpms/flatpak-runtime-config
# branch f44, commit d947a4c82b7a6bfc6079c1848ebee17f9dccc622 (MIT).
# Changes: Release, Luma component data instead of Fedora's, changelog.
# package notes require setting RPM-specific environment variables,
# incompatible with flatpak-builder
%undefine _package_note_flags

Name:           flatpak-runtime-config
Version:        44
Release:        1.luma.1%{?dist}
Summary:        Configuration files that live inside the flatpak runtime
Source1:        50-flatpak.conf
Source2:        sitecustomize.py
Source3:        defaults.json.in
#Source4: org.fedoraproject.Platform.appdata.xml
#Source5: org.fedoraproject.Sdk.appdata.xml
#Source6: org.fedoraproject.KDE5Platform.appdata.xml
#Source7: org.fedoraproject.KDE5Sdk.appdata.xml
#Source8: org.fedoraproject.KDE6Platform.appdata.xml
#Source9: org.fedoraproject.KDE6Sdk.appdata.xml
Source10:       05-flatpak-fontpath.conf
Source11:       app.sh
Source20:       org.projectluma.Platform.appdata.xml
Source21:       org.projectluma.Sdk.appdata.xml

License:        MIT

BuildRequires:  python3
BuildRequires:  python3-rpm-macros

Requires:       findutils
Requires:       fontpackages-filesystem

%if 0%{?fedora}
# Assure the correct identity is always used
Suggests:       fedora-release-flatpak
# Use emacs TUI when building other packages
Suggests:       emacs-nw
# gnuplot requires qt6, gnuplot-wx requires gtk3 (available in all runtimes)
Suggests:       (gnuplot if qt6-qtbase else gnuplot-wx)
# Default version of java, java-devel, java-headless, etc.
Suggests:       java-25-openjdk
Suggests:       java-25-openjdk-devel
Suggests:       java-25-openjdk-headless
# default backend included in runtime
Suggests:       qt6-qtspeech-speechd
# Prefer over wget1-wget for webclient
Suggests:       wget2-wget
%endif

%description
This package includes configuration files that are installed into the flatpak
runtime filesystem during the runtime creation process; it is also installed
into the build root when building RPMs. It contains all configuration
files that need to be different when executing a flatpak.

%prep

%build

%install
rm -rf $RPM_BUILD_ROOT
mkdir -p $RPM_BUILD_ROOT%{_prefix}/cache/fontconfig
mkdir -p $RPM_BUILD_ROOT%{_sysconfdir}/fonts/conf.d
install -t $RPM_BUILD_ROOT%{_sysconfdir}/fonts/conf.d -p -m 0644 %{SOURCE1}
install -t $RPM_BUILD_ROOT%{_sysconfdir}/fonts/conf.d -p -m 0644 %{SOURCE10}

# profile.d to set up command paths
install -D -t $RPM_BUILD_ROOT%{_sysconfdir}/profile.d -p -m 0644 %{SOURCE11}

# usercustomize.py to set up Python paths
for d in %{python3_sitelib} ; do
    mkdir -p $RPM_BUILD_ROOT/$d
    install -t $RPM_BUILD_ROOT/$d -m 0644 %{SOURCE2}
done

# Install appdata for both the Platform and the Sdk
# Luma installs the component data for its own runtime and SDK only.
mkdir -p $RPM_BUILD_ROOT%{_datadir}/metainfo
install -t $RPM_BUILD_ROOT%{_datadir}/metainfo -p -m 0644 %{SOURCE20}
install -t $RPM_BUILD_ROOT%{_datadir}/metainfo -p -m 0644 %{SOURCE21}

# Install flatpak-builder config file
mkdir -p $RPM_BUILD_ROOT%{_sysconfdir}/flatpak-builder
sed -e 's|%%{_libdir}|%{_libdir}|' \
    -e 's|%%{_lib}|%{_lib}|' \
    -e 's|%%{build_cflags}|%{build_cflags}|' \
    -e 's|%%{build_cxxflags}|%{build_cxxflags}|' \
    -e 's|%%{build_ldflags}|%{build_ldflags}|' \
    %{SOURCE3} > $RPM_BUILD_ROOT%{_sysconfdir}/flatpak-builder/defaults.json

mkdir -p $RPM_BUILD_ROOT%{_sysconfdir}/ld.so.conf.d/
cat > $RPM_BUILD_ROOT%{_sysconfdir}/ld.so.conf.d/app.conf <<_EOF
include /run/flatpak/ld.so.conf.d/app-*.conf
include /app/etc/ld.so.conf
include /app/etc/ld.so.conf.d/*.conf
/app/%{_lib}
%if "%{_lib}" != "lib"
/app/lib
%endif
include /run/flatpak/ld.so.conf.d/runtime-*.conf
_EOF

# We duplicate selected file triggers from packages in the runtime, to
# extend them to cover /app as well. Some other functions that RPM file
# triggers normally provide are handled by flatpak triggers - in particular
# calling update-desktop-database and gtk-update-icon-cache.

# The ldconfig scriplets have a limited function since symlinks are supposed
# to be packaged, and a ld.so.cache that handles both /app and /usr is
# maintained by flatpak. But occasionally a symlink is missed in packaging,
# and this will make sure it is created install time, as it would be
# system-wide.

%post -p /usr/bin/ldconfig

%filetriggerin -P 1999999 -- /app/lib /app/lib64 /app/etc/ld.so.conf.d
/usr/bin/ldconfig

%transfiletriggerin -P 99999 -- /app/%{_lib}/gdk-pixbuf-2.0/2.10.0/loaders /usr/%{_lib}/gdk-pixbuf-2.0/2.10.0/loaders
gdk-pixbuf-query-loaders-%{__isa_bits} > /usr/%{_lib}/gdk-pixbuf-2.0/2.10.0/loaders.cache
if [ -d /app/%{_lib}/gdk-pixbuf-2.0/2.10.0/loaders ]; then
    GDK_PIXBUF_MODULEDIR=/app/%{_lib}/gdk-pixbuf-2.0/2.10.0/loaders/ \
        gdk-pixbuf-query-loaders-%{__isa_bits} >> /usr/%{_lib}/gdk-pixbuf-2.0/2.10.0/loaders.cache
    cp /usr/%{_lib}/gdk-pixbuf-2.0/2.10.0/loaders.cache /app/%{_lib}/gdk-pixbuf-2.0/2.10.0/loaders.cache
fi

%transfiletriggerin -- /app/share/glib-2.0/schemas
glib-compile-schemas /app/share/glib-2.0/schemas &> /dev/null || :

%transfiletriggerin -- /app/share/fonts
HOME=/root /usr/bin/fc-cache -s || :

%transfiletriggerin -- /app/share/java
for d in `find /app/share/java -type d`; do mkdir -p /usr${d#/app}; done
for f in `find /app/share/java ! -type d`; do ln -s $f /usr${f#/app}; done

%transfiletriggerin -P 1 -- /app
for l in `find /app -type l`; do \
  case `readlink $l` in /etc/alternatives/*) ln -fns `readlink -f $l` $l ;; esac \
done

%files
%dir %{_prefix}/cache
%dir %{_prefix}/cache/fontconfig
%{python3_sitelib}
%{_datadir}/metainfo/*.appdata.xml
%{_sysconfdir}/flatpak-builder/
%{_sysconfdir}/fonts/conf.d/*
%{_sysconfdir}/ld.so.conf.d/app.conf
%{_sysconfdir}/profile.d/app.sh

%changelog
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 44-1.luma.1
- Rebuild Fedora's flatpak-runtime-config 44 (dist-git d947a4c8) for the Luma
  Platform runtime; ship component data for org.projectluma.Platform and Sdk

