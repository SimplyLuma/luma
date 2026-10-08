# SPDX-License-Identifier: Apache-2.0
# Private qualification only; no shared repository publication.
Name:           python-authlib
Version:        1.7.2
Release:        0.1.luma.experiment%{?dist}
Summary:        Maintained OAuth client support for Luma Connect
License:        BSD-3-Clause
URL:            https://github.com/authlib/authlib
Source0:        https://github.com/authlib/authlib/archive/refs/tags/v%{version}.tar.gz#/authlib-v%{version}-upstream.tar.gz
BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-setuptools
BuildRequires:  python3-wheel
BuildRequires:  python3-pytest
BuildRequires:  python3dist(cryptography)
BuildRequires:  python3dist(joserfc) >= 1.7.5
BuildRequires:  python3dist(httpx) >= 0.28.1
BuildRequires:  python3dist(requests)
BuildRequires:  python3dist(werkzeug)

%description
Upstream Authlib from the maintained 1.7 line, preserving Fedora HTTPX
compatibility. Private dependency qualification, not a composed replacement.

%package -n python3-authlib
Summary:        %{summary}
%description -n python3-authlib
Maintained upstream Python implementation for private Luma Connect qualification.

%prep
%autosetup -n authlib-%{version}

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l authlib

%check
# Normal upstream synchronous OAuth2 HTTPX client tests; no external account.
PYTHONPATH=. PYTHONWARNINGS=error::ResourceWarning %{python3} -m pytest -c /dev/null tests/clients/test_httpx/test_oauth2_client.py

%files -n python3-authlib -f %{pyproject_files}
%doc README.md
