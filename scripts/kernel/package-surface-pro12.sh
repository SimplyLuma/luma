#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Package a completed, task-owned cross build and retain corresponding source.
# Call only after the compiler job has exited. All output stays under OUTPUT.
set -euo pipefail
repo=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
source_dir=$(realpath "${1:?usage: package-surface-pro12.sh SOURCE BUILD FEDORA_CONFIG CACHE OUTPUT}")
build_dir=$(realpath "${2:?}")
base_config=$(realpath "${3:?}")
cache=$(realpath "${4:?}")
output=$(realpath -m "${5:?}")
lock="$repo/config/kernel/surface-pro12-7.3.json"
[ -s "$build_dir/arch/arm64/boot/Image" ] || { echo 'Kernel Image is not complete' >&2; exit 1; }
[ ! -e "$output" ] || { echo 'Refusing to reuse a package output directory' >&2; exit 1; }
[ -z "$(git -C "$repo" status --porcelain --untracked-files=no)" ] || { echo 'Source must be committed before packaging' >&2; exit 1; }
# Verify the exact source archives and all selected patch bodies before reuse.
python3 - "$repo" "$cache" "$lock" <<'PY'
import importlib.util,json,pathlib,sys
repo,cache,lock=map(pathlib.Path,sys.argv[1:])
spec=importlib.util.spec_from_file_location('prepare',repo/'scripts/kernel/prepare-source.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
data=json.loads(lock.read_text());p.validate(data)
for row in data['sources'].values():p.fetch(row,cache,True)
for row in data['patches']:
    if row['source']=='repository' and p.digest(repo/row['path'])!=row['sha256']:
        raise SystemExit('Local patch hash mismatch')
PY
# Bind the cache to the current lock and the replayed source files. The
# caller keeps the original extracted tree frozen between replay and build.
python3 - "$source_dir" "$lock" <<'PYREPLAY'
import hashlib,json,pathlib,sys
source,lock=map(pathlib.Path,sys.argv[1:])
receipt=json.loads((source.parent/'PATCH-REPLAY-VERIFIED.json').read_text())
if receipt['source_lock_sha256']!=hashlib.sha256(lock.read_bytes()).hexdigest():
    raise SystemExit('Fresh-tree replay belongs to another source lock')
for row in receipt['files']:
    path=(source/row['path']).resolve()
    if not path.is_relative_to(source.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
        raise SystemExit('Prepared source differs from its replay receipt')
PYREPLAY
python3 "$repo/scripts/kernel/build-cache.py" verify --repository "$repo" --build "$build_dir" --base-config "$base_config"
mkdir -p "$output"/{SOURCES,SPECS,BUILD,BUILDROOT,RPMS,SRPMS,tmp}
git -C "$repo" archive --format=tar.gz --prefix=luma-surface-sources/ HEAD \
  config/kernel patches/linux-surface-pro12 scripts/kernel packaging/rpm/kernel-surface-pro12.spec \
  LICENSE.md LICENSES > "$output/SOURCES/luma-surface-sources.tar.gz"
for archive in linux-7.3.tar.xz surface-overlay-c570db8.tar.gz; do
  cp --reflink=auto "$cache/$archive" "$output/SOURCES/$archive"
done
cp "$base_config" "$output/SOURCES/fedora-base.config"
cp "$repo/packaging/rpm/kernel-surface-pro12.spec" "$output/SPECS/"
export SOURCE_DATE_EPOCH=$(git -C "$repo" log -1 --format=%ct)
# The source RPM has a complete non-cached build path; prepared paths only
# reuse the same verified source/object tree for this binary build.
rpmbuild -bs --target aarch64 --define "_topdir $output" --define 'dist .fc44' \
  "$output/SPECS/kernel-surface-pro12.spec" > "$output/source-package.log" 2>&1
rpmbuild -bb --target aarch64 --define "_topdir $output" --define "_tmppath $output/tmp" \
  --define 'dist .fc44' --define '_smp_mflags -j6' \
  --define '_luma_cross_compile aarch64-linux-gnu-' \
  --define "_luma_prepared_source $source_dir" --define "_luma_prepared_output $build_dir" \
  "$output/SPECS/kernel-surface-pro12.spec" > "$output/binary-package.log" 2>&1
python3 - "$output" "$lock" "$repo" <<'PY'
import hashlib,json,pathlib,subprocess,sys
out,lock,repo=map(pathlib.Path,sys.argv[1:]);files=sorted([*out.glob('RPMS/**/*.rpm'),*out.glob('SRPMS/*.rpm')])
if len(files)!=2: raise SystemExit('Expected one binary and one source RPM')
rows=[]
for p in files:
    if not p.name.endswith('.src.rpm'):
        metadata=subprocess.check_output(['rpm','-qp','--qf','[%{FILEMODES:octal}\t%{FILEUSERNAME}\t%{FILEGROUPNAME}\t%{FILENAMES}\n]',str(p)],text=True)
        count=0
        for line in metadata.splitlines():
            mode,user,group,path=line.split('\t',3)
            if int(mode,8) not in (0o40755,0o100644) or (user,group)!=('root','root'):
                raise SystemExit('Nonstandard kernel RPM ownership/permissions: '+path)
            count+=1
        print('Verified actual kernel RPM ownership/permissions:',count,'paths')
    with p.open('rb') as f: sha=hashlib.file_digest(f,'sha256').hexdigest()
    identity=subprocess.check_output(['rpm','-qp','--nosignature','--qf','%{NAME} %{VERSION} %{RELEASE} %{ARCH} %{SHA256HEADER}',str(p)],text=True)
    rows.append({'file':str(p.relative_to(out)),'sha256':sha,'identity':identity})
receipt={'schema':'org.projectluma.device-kernel-package/v1',
         'source_revision':subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),
         'source_lock_sha256':hashlib.sha256(lock.read_bytes()).hexdigest(),'files':rows}
(out/'PACKAGES.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(out/'PACKAGES.json')
PY
