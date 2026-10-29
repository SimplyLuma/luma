# SPDX-License-Identifier: Apache-2.0
"""Project creation, edits and bounded builds in Ari's own workspace.

Generated code executes only in a disposable Bubblewrap namespace: no host
home, network, session bus, credentials or system writes. It can edit its project.
"""
from __future__ import annotations

import hashlib
import os
import re
import resource
import signal
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ari.mcp import serve

MAX_FILE = 256 * 1024
MAX_OUTPUT = 32 * 1024
_PROJECT = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\Z")


def root():
    # A readable, user-owned Projects folder, independent of model/account data.
    return Path.home() / "Projects" / "Ari"


def project_path(name, *, create=False, allow_missing=False):
    if not isinstance(name, str) or not _PROJECT.fullmatch(name):
        raise ValueError("Use a project name containing letters, numbers, dashes or underscores.")
    base = root()
    for part in (base.parent, base, base / name):
        if part.is_symlink():
            raise ValueError("Project folders cannot be symbolic links.")
    if create:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = base / name
    if not create and not allow_missing and not target.is_dir():
        raise ValueError("That project does not exist. Create it first.")
    return target


def file_path(project, value):
    relative = PurePosixPath(value)
    if not value or relative.is_absolute() or any(part in ("..", ".git") for part in relative.parts):
        raise ValueError("Choose a relative file path inside the project.")
    path = project
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Project files cannot follow symbolic links.")
    return path


def list_project(arguments):
    name = arguments.get("project")
    if not name:
        base = root()
        values = sorted(p.name for p in base.iterdir() if p.is_dir() and not p.is_symlink()) if base.exists() else []
        return {"ok": True, "summary": "Read Ari projects.", "data": {"folder": str(base), "projects": values}}
    target = project_path(name)
    values = []
    for folder, dirs, files in os.walk(target, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in (".git", ".venv", "node_modules", "__pycache__")
                         and not (Path(folder) / d).is_symlink())
        values.extend(str((Path(folder) / name).relative_to(target)) for name in sorted(files)
                      if not (Path(folder) / name).is_symlink())
        if len(values) >= 500:
            break
    return {"ok": True, "summary": f"Read files in {name}.", "data": values[:500], "untrusted": True}


def read_project_file(arguments):
    path = file_path(project_path(arguments["project"]), arguments["path"])
    with path.open("rb") as stream:
        content = stream.read(MAX_FILE + 1)
    if len(content) > MAX_FILE:
        raise ValueError("That file is too large to read at once.")
    return {"ok": True, "summary": f"Read {arguments['path']}.", "untrusted": True,
            "data": {"content": content.decode("utf-8"), "sha256": hashlib.sha256(content).hexdigest()}}


def write_project_file(arguments):
    target = file_path(project_path(arguments["project"]), arguments["path"])
    content = arguments["content"].encode("utf-8")
    if len(content) > MAX_FILE:
        raise ValueError("Write at most 256 KB per file.")
    exists = target.exists()
    expected = arguments.get("expected_sha256", "")
    if exists:
        if not target.is_file() or target.stat().st_nlink != 1:
            raise ValueError("Only ordinary project files can be replaced.")
        with target.open("rb") as stream:
            current = stream.read(MAX_FILE + 1)
        if len(current) > MAX_FILE or hashlib.sha256(current).hexdigest() != expected:
            return {"ok": False, "summary": "The file changed or was not read first. Read it again before replacing it."}
    elif expected:
        return {"ok": False, "summary": "That file no longer exists. Read the project again."}
    if arguments.get("preview"):
        return {"ok": True, "preview": f"{'Replace' if exists else 'Create'} {arguments['path']} in {arguments['project']}",
                "detail": arguments["content"]}
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement also prevents mutating another hard link. Exclusive
    # new-file publication never overwrites an existing concurrent writer.
    descriptor, temporary = tempfile.mkstemp(prefix=".ari-write-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if exists:
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError("The file changed during the edit. Read it again.")
            os.replace(temporary, target)
        else:
            os.link(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"ok": True, "summary": f"{'Updated' if exists else 'Created'} {arguments['path']} in {arguments['project']}.",
            "data": {"sha256": hashlib.sha256(content).hexdigest(), "path": str(target)}, "open_path": str(target)}


def create_project(arguments):
    target = project_path(arguments["project"], create=not arguments.get("preview"), allow_missing=True)
    if target.exists():
        return {"ok": False, "summary": "That project already exists. Choose another name or edit it."}
    app_id = arguments["application_id"]
    from luma_sdk.validation import application_id_errors
    errors = application_id_errors(app_id)
    if errors:
        return {"ok": False, "summary": "; ".join(errors)}
    if arguments.get("preview"):
        return {"ok": True, "preview": f"Create {arguments['name']}", "detail": str(target)}
    # The SDK is the single owner of app templates, IDs and native contracts.
    from argparse import Namespace
    from contextlib import redirect_stdout
    import io
    from luma_sdk.cli import _cmd_new
    with redirect_stdout(io.StringIO()):
        code = _cmd_new(Namespace(name=arguments["name"], app_id=app_id, destination=str(target)))
    if code != 0:
        return {"ok": False, "summary": "The Luma SDK could not create that project."}
    return {"ok": True, "summary": f"Created {arguments['name']}.", "data": {"path": str(target), "project": arguments["project"]}, "open_path": str(target)}


def sandbox_command(target, command):
    if not isinstance(command, list) or not command or len(command) > 64 or any(
            not isinstance(item, str) or "\0" in item or len(item) > 8192 for item in command):
        raise ValueError("Use an argument array for the project command.")
    # Never pass host env (proxy/API keys/SSH agent) or read the real home or
    # /run. Mount only immutable tools and this one project into the namespace.
    args = ["/usr/bin/bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL",
            "--clearenv", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp/home",
            "--setenv", "LANG", "C.UTF-8", "--ro-bind", "/usr", "/usr"]
    for alias in ("bin", "sbin", "lib", "lib64"):
        source = Path("/") / alias
        if source.is_symlink():
            args += ["--symlink", os.readlink(source), str(source)]
        elif source.exists():
            args += ["--ro-bind", str(source), str(source)]
    return args + ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home",
                   "--dir", "/etc", "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache",
                   "--bind", str(target), "/workspace", "--chdir", "/workspace", "--", *command]


def run_project(arguments):
    target = project_path(arguments["project"])
    command = arguments["command"]
    args = sandbox_command(target, command)
    if arguments.get("preview"):
        import shlex
        return {"ok": True, "preview": f"Run a command in {arguments['project']}",
                "detail": shlex.join(command) + "\nOnly this project can be changed. No network or access to your other files."}
    # Bound output on disk and kill the whole process group on timeout, including
    # forked build helpers. Generated code must not hold a tool server forever.
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024 * 1024, 128 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                   env={"PATH": "/usr/bin:/bin"}, start_new_session=True, preexec_fn=limits)
        try:
            code = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            code = -signal.SIGKILL
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        output.seek(0)
        log = output.read(MAX_OUTPUT + 1).decode("utf-8", errors="replace")
    return {"ok": code == 0,
            "summary": f"Project command {'completed' if code == 0 else 'failed'} in {arguments['project']} (exit {code}).",
            "data": {"exit_code": code, "output": log[:MAX_OUTPUT], "truncated": len(log) > MAX_OUTPUT}, "untrusted": True}


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


STRING = {"type": "string"}
PREVIEW = {"type": "boolean"}
TOOLS = {
    "list_project": ("List Ari's projects, or files inside one project. Projects live in ~/Projects/Ari; other personal files are not exposed.",
                     schema({"project": STRING}), list_project),
    "read_project_file": ("Read a UTF-8 source file inside an Ari project, including its current sha256 needed before replacing it.",
                          schema({"project": STRING, "path": STRING}, ["project", "path"]), read_project_file),
    "write_project_file": ("Create or replace a source file inside an Ari project. Read existing files first and pass their exact expected_sha256 to avoid overwriting another edit.",
                           schema({"project": STRING, "path": STRING, "content": STRING, "expected_sha256": STRING, "preview": PREVIEW},
                                  ["project", "path", "content"]), write_project_file),
    "create_project": ("Create a real native Luma application project from the installed SDK template. Then list/read/edit its source, run checks and builds with run_project. Never report a finished application from scaffolding alone.",
                       schema({"project": STRING, "name": STRING, "application_id": STRING, "preview": PREVIEW},
                              ["project", "name", "application_id"]), create_project),
    "run_project": ("Run an argument-array command to check, test, build or package an Ari project in an isolated workspace. No network, user credentials, session bus, other home files or system writes. Commands and output are limited. Report actual failures and iterate on source.",
                    schema({"project": STRING, "command": {"type": "array", "items": STRING}, "preview": PREVIEW}, ["project", "command"]), run_project),
}

if __name__ == "__main__":
    serve("ari-workspace", TOOLS)
