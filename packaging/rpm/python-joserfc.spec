# SPDX-License-Identifier: Apache-2.0
# Private qualification only; no shared repository publication.
Name:           python-joserfc
Version:        1.7.5
Release:        0.1.luma.experiment%{?dist}
Summary:        Maintained JOSE implementation for Luma Connect validation
License:        BSD-3-Clause
URL:            https://github.com/authlib/joserfc
Source0:        https://files.pythonhosted.org/packages/source/j/joserfc/joserfc-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-setuptools
BuildRequires:  python3-wheel
BuildRequires:  python3-pytest
BuildRequires:  python3dist(cryptography) >= 45.0.1

%description
Upstream JOSE implementation, packaged without vendoring for a private
Connect dependency qualification. No algorithms or cryptography are patched.

%package -n python3-joserfc
Summary:        %{summary}
%description -n python3-joserfc
Maintained upstream Python implementation for private Luma Connect qualification.

%prep
%autosetup -n joserfc-%{version}

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l joserfc

%check
# The JWK/JWS/JWT suites cover the key/signature/claims surface used by Connect.
# JWE/draft algorithms are outside this qualification, not claimed tested.
PYTHONPATH=src PYTHONWARNINGS=error::ResourceWarning %{python3} -m pytest -c /dev/null tests/jwk tests/jws tests/jwt

%files -n python3-joserfc -f %{pyproject_files}
%doc README.rst
