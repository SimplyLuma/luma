# SPDX-License-Identifier: Apache-2.0
"""Architecture-scoped metadata beside a single shared OSTree repository."""
from pathlib import Path

ARCHITECTURES = ('x86_64', 'aarch64')

def architecture(value):
    if value not in ARCHITECTURES:
        raise ValueError(f'Unsupported desktop architecture: {value}')
    return value

def architecture_path(root, arch='x86_64'):
    architecture(arch)
    return Path(root) if arch == 'x86_64' else Path(root) / arch

def releases_path(repo, arch='x86_64'):
    return architecture_path(Path(repo) / 'luma/releases', arch)

def manifest_architecture(manifest):
    arch = architecture(manifest.get('architecture', 'x86_64'))
    ref = manifest.get('ref')
    if ref and (len(ref.split('/')) < 4 or ref.split('/')[-2] != arch):
        raise ValueError('Release architecture and signed ref disagree')
    return arch

def graph_prefix(arch='x86_64'):
    return str(architecture_path('os/graph', arch))
