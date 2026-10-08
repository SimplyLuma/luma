# SPDX-License-Identifier: Apache-2.0
"""Preserve unrelated UI preferences and the original bytes before first edit."""
import json
import os
from pathlib import Path
import tempfile


def _decode(raw):
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('Monitor preferences must be an object')
    return value


def read_preferences(path):
    try:return _decode(Path(path).read_bytes())
    except FileNotFoundError:return {}


def _temporary(path,raw):
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix=path.name+'.',suffix='.tmp',delete=False) as stream:
        temporary=Path(stream.name)
        try:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True);raise
    return temporary


def update_preferences(path,changes):
    path=Path(path)
    try:original=path.read_bytes()
    except FileNotFoundError:original=None
    previous=_decode(original) if original is not None else {}
    updated=previous|changes
    if original is not None and updated==previous:return False
    encoded=(json.dumps(updated,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    path.parent.mkdir(parents=True,exist_ok=True)
    if original is not None:
        backup=path.with_suffix(path.suffix+'.bak')
        temporary=_temporary(path,original)
        try:
            try:os.link(temporary,backup)
            except FileExistsError:pass
        finally:temporary.unlink(missing_ok=True)
    temporary=_temporary(path,encoded)
    try:os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    return True
