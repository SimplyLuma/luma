#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Assemble a Flatpak runtime or runtime extension from Fedora and Luma RPMs.

This is the step Fedora's Koji performs when it builds org.fedoraproject.Platform:
install a package set into an empty root with dnf, run the container.yaml
cleanup commands, map /usr to files/ and /etc to files/etc, run
`flatpak build-finish` with the declared extension points and environment, and
commit the tree. The mapping, metadata and OCI packaging are done by the exact
code Fedora uses (flatpak_module_tools.ContainerBuilder and FlatpakBuilder).
The only thing replaced is where packages come from: Koji tags become a Fedora
44 mirror plus Luma's local package repository, which carries Luma's GTK 4,
libadwaita and developer platform at a higher priority than Fedora's builds.

Run as root inside the luma-depot-tools container (it mounts /proc, /sys and
/dev into the install root). Outputs, under --resultdir:

  <nvr>.<arch>.oci.tar            Fedora-compatible OCI Flatpak image
  <nvr>.<arch>.oci.rpmlist.json   every RPM in the tree, for provenance
  <nvr>.<arch>.oci.{manifest,config}.json
  ostree/                         archive repo holding the same commit, ready
                                  for `flatpak build-commit-from`
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from flatpak_module_tools.build_context import BuildContext
from flatpak_module_tools.container_builder import ContainerBuilder, InnerExcutor
from flatpak_module_tools.container_spec import ContainerSpec
from flatpak_module_tools.flatpak_builder import FLATPAK_METADATA_BOTH
from flatpak_module_tools.utils import Arch
from private_builder_reserve import configure as configure_private_reserve
from private_builder_reserve import verify as verify_private_reserve

FEDORA_MIRROR = "https://dl.fedoraproject.org/pub/fedora/linux"


class StaticRepo:
    """A dnf repository definition that is not backed by Koji."""

    def __init__(self, repo_id: str, baseurl: str):
        self.tag_name = repo_id
        self.baseurl = baseurl

    def dnf_config(self, priority=None, includepkgs=None):
        text = dedent(f"""\
            [{self.tag_name}]
            name={self.tag_name}
            baseurl={self.baseurl}
            enabled=1
            skip_if_unavailable=False
        """)
        if priority is not None:
            text += f"priority={priority}\n"
        if includepkgs is not None:
            text += "includepkgs=" + ",".join(includepkgs) + "\n"
        return text


class ReleaseProfile:
    @staticmethod
    def release_from_runtime_version(runtime_version: str) -> str:
        return "".join(ch for ch in runtime_version if ch.isdigit())


class LumaBuildContext(BuildContext):
    def __init__(self, *, container_spec, nvr, fedora_release, mirror, local_repo,
                 runtime_packages):
        # local_repo stays None for the base class: flatpak-module-tools would
        # otherwise try to bind-mount it into a mock chroot. dnf runs in this
        # container with --installroot, so the host path is used directly.
        super().__init__(profile=ReleaseProfile(), arch=Arch(),
                         container_spec=container_spec, local_repo=None)
        self._luma_repo = local_repo
        self._nvr = nvr
        arch = self.arch.rpm
        self._fedora = StaticRepo(
            "fedora", f"{mirror}/releases/{fedora_release}/Everything/{arch}/os/")
        self._updates = StaticRepo(
            "updates", f"{mirror}/updates/{fedora_release}/Everything/{arch}/")
        self._runtime_packages = runtime_packages

    @property
    def nvr(self):
        return self._nvr

    @property
    def runtime_archive(self):
        raise NotImplementedError("Luma builds never look up a Koji runtime archive")

    @property
    def runtime_package_repo(self):
        return self._fedora

    @property
    def app_package_repo(self):
        return self._fedora

    @property
    def app_build_repo(self):
        return self._fedora

    def get_repos(self, *, for_container, local_repo_path=None):
        # Lower number wins in dnf: Luma's own builds shadow Fedora's packages
        # of the same name whatever their version, so a Fedora update to GTK
        # can never silently replace Luma's toolkit inside the runtime.
        repos = [
            self._fedora.dnf_config(priority=50),
            self._updates.dnf_config(priority=50),
        ]
        if self._luma_repo:
            repos.append(StaticRepo("luma", f"file://{self._luma_repo}")
                         .dnf_config(priority=10))
        return repos


class LumaContainerBuilder(ContainerBuilder):
    """Two differences from Fedora's ContainerBuilder.

    Packages are installed in one dnf transaction. Upstream installs
    flatpak-runtime-config on its own first, which works in Koji because the
    runtime package tag holds only the runtime's packages; against a full Fedora
    mirror that lone transaction pulls in coreutils where the runtime lists
    coreutils-single, and the two conflict.

    Extension cleanup scripts get the runtime's package list in
    /tmp/runtime-packages.txt, so they can keep only what the runtime lacks.
    """

    def __init__(self, context, runtime_packages):
        super().__init__(context, flatpak_metadata=FLATPAK_METADATA_BOTH)
        self.runtime_packages = runtime_packages

    def _install_packages(self, builder):
        installroot = self.executor.installroot
        packages = builder.get_install_packages()
        if "flatpak-runtime-config" not in packages:
            packages = ["flatpak-runtime-config", *packages]
        package_str = " ".join(shlex.quote(p) for p in packages)
        install_sh = dedent(f"""\
            for i in /proc /sys /dev /var/cache/dnf ; do
                mkdir -p {installroot}/$i
                mount --rbind $i {installroot}/$i
            done
            dnf --installroot={installroot} install -y {package_str}
            """)
        self.executor.write_file(Path("/tmp/install.sh"), install_sh)
        self.executor.check_call(["/bin/bash", "-ex", "/tmp/install.sh"], enable_network=True)

    def _cleanup_tree(self, builder):
        tmp = self.executor.installroot / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "runtime-packages.txt").write_text(
            "".join(f"{name}\n" for name in sorted(self.runtime_packages)))
        super()._cleanup_tree(builder)


def release_install_root_mounts(workdir: Path) -> None:
    """Undo the recursive /proc, /sys, /dev and dnf cache binds.

    flatpak-module-tools binds them into the install root and never removes
    them (mock tears down its chroot instead). Deleting a work directory that
    still holds them would walk into the live /dev, so every path that removes
    a work directory calls this first and refuses to continue if a mount
    survives.
    """
    root = (workdir / "root").resolve()
    mounts = subprocess.run(["findmnt", "-rn", "-o", "TARGET"], check=True,
                            capture_output=True, text=True).stdout.split()
    held = sorted((m for m in mounts if m == str(root) or m.startswith(str(root) + "/")),
                  key=len, reverse=True)
    for target in held:
        subprocess.run(["umount", "-R", target], check=False)
    mounts = subprocess.run(["findmnt", "-rn", "-o", "TARGET"], check=True,
                            capture_output=True, text=True).stdout.split()
    if any(m.startswith(str(root) + "/") for m in mounts):
        raise SystemExit(f"refusing to continue: mounts remain below {root}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--containerspec", type=Path, required=True)
    parser.add_argument("--nvr", required=True,
                        help="name-version-release label, e.g. luma-platform-44-20260915.1")
    parser.add_argument("--fedora-release", default="44")
    parser.add_argument("--mirror", default=FEDORA_MIRROR)
    parser.add_argument("--local-repo", type=Path, required=True,
                        help="createrepo_c repository with Luma RPMs for this architecture")
    parser.add_argument("--runtime-rpmlist", type=Path,
                        help="rpmlist.json of the runtime an extension is built for")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--resultdir", type=Path, required=True)
    parser.add_argument("--private-min-free-bytes", type=int, choices=(10737418240,),
                        help="absolute reserve for this new private unsigned repository")
    args = parser.parse_args()

    spec = ContainerSpec(str(args.containerspec))
    runtime_packages: list[str] = []
    if args.runtime_rpmlist:
        runtime_packages = [entry["name"] for entry in json.loads(args.runtime_rpmlist.read_text())]

    if args.workdir.exists():
        release_install_root_mounts(args.workdir)
    for path in (args.workdir, args.resultdir):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
    installroot = args.workdir / "root"
    installroot.mkdir()
    oci_workdir = args.workdir / "oci"
    oci_workdir.mkdir()
    if args.private_min_free_bytes is not None:
        configure_private_reserve(oci_workdir / "repo", args.private_min_free_bytes)

    context = LumaBuildContext(
        container_spec=spec,
        nvr=args.nvr,
        fedora_release=args.fedora_release,
        mirror=args.mirror,
        local_repo=args.local_repo.resolve(),
        runtime_packages=runtime_packages,
    )
    builder = LumaContainerBuilder(context, runtime_packages)
    executor = InnerExcutor(
        context=context,
        installroot=installroot,
        workdir=oci_workdir,
        releasever=args.fedora_release,
        runtimever=spec.flatpak.branch,
    )
    try:
        builder._run_build(executor, workdir=oci_workdir, resultdir=args.resultdir)
    finally:
        release_install_root_mounts(args.workdir)

    if args.private_min_free_bytes is not None:
        verify_private_reserve(oci_workdir / "repo")

    # FlatpakBuilder committed the tree into an archive repo before exporting
    # the OCI image; keep that repo so publication can use
    # `flatpak build-commit-from` without a second import.
    shutil.move(str(oci_workdir / "repo"), str(args.resultdir / "ostree"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
