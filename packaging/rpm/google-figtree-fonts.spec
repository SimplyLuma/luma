# SPDX-License-Identifier: MIT

%global foundry         google
%global fontfamily      Figtree
%global fontsummary     Friendly geometric sans-serif variable font
%global fontlicense     OFL-1.1
%global fontlicenses    OFL.txt
%global fonts           Figtree*.ttf
%global fontconfs       %{SOURCE10}
%global fontdescription %{expand:
Figtree is a friendly geometric sans-serif family designed by Erik Kennedy.
This package contains the upright and italic variable fonts with a continuous
weight axis from 300 through 900.}

Version: 2.0.3
Release: 1.luma.4%{?dist}
URL:     https://github.com/erikdkennedy/figtree

# Bytes are pinned from Google Fonts commit
# a60a77e14f28abd4ef243a1b5dfc48df0cec5205 and verified by the build script.
Source0: Figtree[wght].ttf
Source1: Figtree-Italic[wght].ttf
Source2: OFL.txt
Source10: 99-luma-figtree-fonts.conf
Source20: adjust-figtree-ui-metrics.py

BuildRequires: python3-fonttools

%fontpkg

%prep
%autosetup -c -T
install -p -m 0644 %{SOURCE0} .
install -p -m 0644 %{SOURCE1} .
install -p -m 0644 %{SOURCE2} .

%build
# Figtree has no U+2010 or U+2011: map both to U+002D's glyph (outline, advance and variation deltas
# unchanged), so "Wi\u2011Fi" draws in Figtree and keeps its line box.
for face in Figtree*.ttf; do
  python3 %{SOURCE20} --hyphens-only "$face" "$face.new"
  mv -f "$face.new" "$face"
done
%fontbuild

%install
%fontinstall

%check
%fontcheck

%fontfiles

%changelog
* Tue Sep 29 2026 Project Luma <project-luma@localhost> - 2.0.3-1.luma.4
- Map U+2010 and U+2011 to the hyphen glyph so the non-breaking hyphen draws in Figtree

* Sun Aug 23 2026 Project Luma <project-luma@localhost> - 2.0.3-1.luma.3
- Order the system sans preference after Fedora language fallbacks

* Tue Aug 11 2026 Project Luma <project-luma@localhost> - 2.0.3-1.luma.2
- Load the system sans alias after Fedora language defaults for fresh accounts

* Sun Aug 09 2026 Project Luma <project-luma@localhost> - 2.0.3-1.luma.1
- Package the pinned Figtree variable family as Luma's preferred sans font
