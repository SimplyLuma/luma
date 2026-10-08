# SPDX-License-Identifier: MIT

%global foundry         google
%global fontfamily      Caveat
%global fontsummary     Handwriting variable font
%global fontlicense     OFL-1.1
%global fontlicenses    OFL.txt
%global fonts           Caveat*.ttf
%global fontdescription %{expand:
Caveat is a handwriting family drawn by Impallari Type. This package contains
the variable font with a continuous weight axis from 400 through 700, and it
is the note body face used by Luma's Sticky Notes.

This package deliberately ships no generic font alias. Applications ask for
the Caveat family by name; it never becomes the default sans-serif, interface
or cursive family for the desktop.}

Version: 2.000
Release: 1.luma.1%{?dist}
URL:     https://github.com/googlefonts/caveat

# Bytes are pinned from Google Fonts commit
# 5571d84c0d8c70ec1af4f64072d8c5cf1e4e9643 and verified by the build script.
Source0: Caveat[wght].ttf
Source1: OFL.txt

%fontpkg

%prep
%autosetup -c -T
install -p -m 0644 %{SOURCE0} .
install -p -m 0644 %{SOURCE1} .

%build
%fontbuild

%install
%fontinstall

%check
%fontcheck

%fontfiles

%changelog
* Fri Sep 11 2026 Project Luma <project-luma@localhost> - 2.000-1.luma.1
- Package the pinned Caveat variable family as the Sticky Notes note body face
