#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Hardware-free C1 secure-runtime gate using QREL's exact qseecomd inside the
# Android container.  This deliberately replaces the earlier split design
# (Linux qsee-supplicant plus Android KeyMint): qseecomd owns the stock Mink
# callback/listener graph and publishes listener readiness itself.  Only the
# object-based QCOMTEE client is exposed.  Modem, audio, GPU, input, raw block,
# RPMB, and privileged TEE devices remain unavailable.

set -Eeuo pipefail
umask 077

name=luma-stock-android-c1
lxc_path=/var/lib/lxc
state=/var/lib/luma/stock-android-container1
config=$lxc_path/$name/config
manifest=$state/manifest.json
module=/var/tmp/luma-stock-qrel1695-keymint-gate1/qcomtee.ko
qseecomd_mink_adapter=/var/tmp/luma-stock-qseecomd-c1-adapter/libminkdescriptor.so
qseecomd_dmabuf_adapter=/var/tmp/luma-stock-qseecomd-c1-adapter/libdmabufheap.so
selinux_disabled_shim=/var/tmp/luma-stock-android-container-framework1/libluma_selinux_disabled.so
restorecon_disabled_shim=/var/tmp/luma-stock-android-container-framework1/libluma_restorecon_disabled.so
override=$state/mounts/vendor/etc/init/zzz-luma-stock-qseecomd-c1.rc
apex_override=$state/mounts/system/system/etc/init/zzz-luma-stock-apexd-c1.rc
apex_runtime=/var/tmp/luma-stock-qrel1695-apex-runtime2
apex_inventory=$apex_runtime/metadata/activated-apex-inventory.json
apex_hash_manifest=$apex_runtime/metadata/HASHES.sha256
apex_partition_hashes=$apex_runtime/metadata/partition-images.sha256
permission_controller_source_dir=$apex_runtime/mounts/com.android.permission@360673420/priv-app/GooglePermissionController@360673420
permission_controller_source=$permission_controller_source_dir/GooglePermissionController.apk
permission_controller_target=$state/mounts/system/system/priv-app/LumaGooglePermissionController
permission_controller_allowlist_source=$apex_runtime/mounts/com.android.permission@360673420/etc/permissions/privapp_allowlist_com.google.android.permissioncontroller.xml
permission_controller_allowlist_target=$state/mounts/system/system/etc/permissions/luma-privapp-allowlist-com.google.android.permissioncontroller.xml
extservices_source_dir=$apex_runtime/mounts/com.android.extservices@360671263/priv-app/GoogleExtServices@360671260
extservices_source=$extservices_source_dir/GoogleExtServices.apk
extservices_target=$state/mounts/system/system/priv-app/LumaGoogleExtServices
extservices_allowlist_source=$apex_runtime/mounts/com.android.extservices@360671263/etc/permissions/privapp_allowlist_com.google.android.ext.services.xml
extservices_allowlist_target=$state/mounts/system/system/etc/permissions/luma-privapp-allowlist-com.google.android.ext.services.xml
sdk_sandbox_source_dir=$apex_runtime/mounts/com.android.adservices@360650020/app/SdkSandboxGoogle@360650020
sdk_sandbox_source=$sdk_sandbox_source_dir/SdkSandboxGoogle.apk
sdk_sandbox_target=$state/mounts/system/system/app/LumaSdkSandboxGoogle
connectivity_resources_source_dir=$apex_runtime/mounts/com.android.tethering@360671220/priv-app/ServiceConnectivityResourcesGoogle@360671220
connectivity_resources_source=$connectivity_resources_source_dir/ServiceConnectivityResourcesGoogle.apk
connectivity_resources_target=$state/mounts/system/system/priv-app/LumaNetRes
stock_sensors_multihal=$state/mounts/vendor/bin/hw/android.hardware.sensors-service.multihal
stock_netd=$state/mounts/system/system/bin/netd
stock_netd_rc=$state/mounts/system/system/etc/init/netd.rc
stock_netbpfload=$apex_runtime/mounts/com.android.tethering@360671220/bin/netbpfload
stock_netbpfload_rc=$apex_runtime/mounts/com.android.tethering@360671220/etc/netbpfload.35rc
stock_netd_bpf_object=$apex_runtime/mounts/com.android.tethering@360671220/etc/bpf/netd_shared/netd.o
stock_clatd_bpf_object=$apex_runtime/mounts/com.android.tethering@360671220/etc/bpf/net_shared/clatd.o
stock_dscp_policy_bpf_object=$apex_runtime/mounts/com.android.tethering@360671220/etc/bpf/net_shared/dscpPolicy.o
stock_service_connectivity_jar=$apex_runtime/mounts/com.android.tethering@360671220/javalib/service-connectivity.jar
stock_platform_bpfloader=$state/mounts/system/system/bin/bpfloader
stock_audioserver=$state/mounts/system/system/bin/audioserver
stock_audioserver_rc=$state/mounts/system/system/etc/init/audioserver.rc
stock_vendor_audio_hal=$state/mounts/vendor/bin/hw/android.hardware.audio.service_64
stock_vendor_audio_hal_rc=$state/mounts/vendor/etc/init/android.hardware.audio.service_64.rc
stock_default_audio_hal=$state/mounts/vendor/lib64/hw/audio.primary.default.so
stock_gpuservice=$state/mounts/system/system/bin/gpuservice
stock_gpuservice_rc=$state/mounts/system/system/etc/init/gpuservice.rc
netbpfload_adapter_source=/var/tmp/luma-c1-netbpfload/luma-netbpfload
netbpfload_adapter_target=$state/mounts/system/system/bin/luma-netbpfload
connectivity_adapter_source=/var/tmp/luma-c1-connectivity-adapter/libservice-connectivity.so
service_connectivity_adapter_source=/var/tmp/luma-c1-connectivity-adapter/service-connectivity.jar
netfilter_custom_module_dir=/var/tmp/luma-c1-netfilter-stage
empty_sensors_config=$state/state/c1-empty-sensors-hals.conf
minimal_sensors_manifest=/var/tmp/luma-c1-minimal-vintf/android.hardware.sensors-multihal.xml
minimal_empty_permissions=/var/tmp/luma-c1-minimal-vintf/empty-permissions.xml
framework_audio_manifest=/var/tmp/luma-c1-minimal-vintf/framework-audio-manifest.xml
stock_non_qmaa_manifest=$state/mounts/vendor/etc/vintf/manifest/manifest_non_qmaa.xml
ssd_scratch=$state/state/qseecomd-c1-ssd
vendor_dmabuf_dir=$state/mounts/vendor/lib64/luma-qsee
vendor_dmabuf_target=$vendor_dmabuf_dir/libdmabufheap.so
system_mink_target=$state/mounts/system/system/lib64/libminkdescriptor.so
selinux_disabled_target=$state/mounts/system/system/lib64/libluma_selinux_disabled.so
restorecon_disabled_target=$state/mounts/system/system/lib64/libluma_restorecon_disabled.so
angle_egl_source=$state/mounts/vendor/lib64/egl/libEGL_angle.so
angle_gles1_source=$state/mounts/vendor/lib64/egl/libGLESv1_CM_angle.so
angle_gles2_source=$state/mounts/vendor/lib64/egl/libGLESv2_angle.so
angle_egl_target=$state/mounts/vendor/lib64/egl/libEGL_luma_null.so
angle_gles1_target=$state/mounts/vendor/lib64/egl/libGLESv1_CM_luma_null.so
angle_gles2_target=$state/mounts/vendor/lib64/egl/libGLESv2_luma_null.so
angle_probe_source=/var/tmp/luma-stock-android-headless/luma-angle-null-probe
angle_probe_target=$state/mounts/vendor/bin/luma-angle-null-probe
swangle_probe_source=/var/tmp/luma-stock-android-headless/luma-swangle-pastel-probe
swangle_probe_target=$state/mounts/vendor/bin/luma-swangle-pastel-probe
swangle_egl_target=$state/mounts/vendor/lib64/egl/libEGL_luma_swangle.so
swangle_gles1_target=$state/mounts/vendor/lib64/egl/libGLESv1_CM_luma_swangle.so
swangle_gles2_target=$state/mounts/vendor/lib64/egl/libGLESv2_luma_swangle.so
pastel_source=$state/mounts/vendor/lib64/hw/vulkan.pastel.so
noop_composer_source=/var/tmp/luma-aosp-android36-noop-composer
noop_composer_target=$state/mounts/vendor/bin/hw/android.hardware.graphics.composer3-service.ranchu
noop_composer_lib_dir_target=$state/mounts/vendor/lib64/luma-noop-composer
stock_allocator=$state/mounts/vendor/bin/hw/vendor.qti.hardware.display.allocator-service
stock_allocator_rc=$state/mounts/vendor/etc/init/vendor.qti.hardware.display.allocator-service.rc
system_heap_module=/var/tmp/luma_dma_heap_system.ko
noop_composer_lib_names=(
  android.hardware.common-V2-ndk.so
  android.hardware.drm.common-V1-ndk.so
  android.hardware.graphics.allocator-V2-ndk.so
  android.hardware.graphics.allocator@2.0.so
  android.hardware.graphics.allocator@3.0.so
  android.hardware.graphics.allocator@4.0.so
  android.hardware.graphics.common-V6-ndk.so
  android.hardware.graphics.common@1.0.so
  android.hardware.graphics.common@1.1.so
  android.hardware.graphics.common@1.2.so
  android.hardware.graphics.composer3-V4-ndk.so
  android.hardware.graphics.composer@2.1-resources.so
  android.hardware.graphics.composer@2.2-resources.so
  android.hardware.graphics.mapper@2.0.so
  android.hardware.graphics.mapper@2.1.so
  android.hardware.graphics.mapper@3.0.so
  android.hardware.graphics.mapper@4.0.so
  lib_renderControl_enc.so
  libOpenglSystemCommon.so
  libOpenglCodecCommon.so
  libGLESv1_enc.so
  libGLESv2_enc.so
  libbase.so
  libc++.so
  libcutils.so
  libgralloctypes.so
  libhardware.so
  libhidlbase.so
  libjsoncpp.so
  libui.so
  libutils.so
)
adapter_diagnostic_log=$state/state/data/luma-qcomtee-adapter.log
expected_kernel=7.1.2-luma-fp-ims1
expected_manifest_sha=65a317645db47fb0dfa7b2b8f3ee9b923fa03a543ece43deb158c2e0c14ebfd8
expected_config_sha=db477c4d10c1511d1444c91a093fe8c4cca3a506a263f5177b33c26b23c1ae6f
expected_module_sha=54b336ee9c420173ef2419c5bd2d1d4192c72c485d3a2e3ba7f85c32e2019ab7
expected_qseecomd_sha=67caf49683755d103ad56a2661d06889c2482a6e7493cb167305d36f021d83b9
expected_qseecomd_rc_sha=072f7b6cb8c88d556296b1b603cc3a2573d5064e26cab6b0e35a12d25f692d45
expected_qseecom_api_sha=e60d77891ff4c0949f2d539a10ed83b6185c680c2ab162e69ecb8b2f98ad2fe7
expected_rpmb_sha=45c219f0ee1c45f6ef7420e8bf82d5b158789fb9578e3b9e03cd7fc90669f1ea
expected_ssd_sha=667b6ec360b6a14538a231379966726ea6b1e4c2a209d808aa4a0d509679b253
expected_drmtime_sha=cbacdc2024f48a296be91c70e38fa207017a64767fe44d32626315838f94a424
expected_mink_adapter_sha=8666a4a7092175a35b764dc7f4446e0d2b5e82b58670f52166d13d068f7247cb
expected_dmabuf_adapter_sha=a259ecaaf82509b266f564da48a9ef637d7f835e315437b6f0583c605b8a5a93
expected_selinux_disabled_shim_sha=e8065b3dfe49ef72376e74d40fa45a1cb370b50cc00889b25178ddfd3ba776cf
expected_restorecon_disabled_shim_sha=5631fa1c1f7b2ea022644ef065f08c6db9e3fc7fc5765352c4fe9db2e01293b2
expected_minimal_sensors_manifest_sha=1885d79263d0fd3342424218e266c3acf47a8e5dc3edc9702d116dd446a94ed6
expected_minimal_empty_permissions_sha=f9b04dc5a88459399a8870155888d49ba6ce0f3fff2ce92a26482e61baaaaefd
expected_framework_audio_manifest_sha=28f5a3ca3b54f119d694cd88210e8467b81acdf772c2734218321c2ad49e3a3f
expected_stock_non_qmaa_manifest_sha=b13c91cc26c21aceb043d98b605c144be3f437bc887c8e4e7e65a6fba7af1351
expected_surfaceflinger_sha=f315acbf39483488c0dca30f8ca95ff01854b33c383125ea8705dd16798231e8
expected_surfaceflinger_rc_sha=c6f8279f4fff66ff9add26a521a01dbd563a38576c0f85e2c7d88de78e2c167d
expected_angle_egl_sha=6ea0abff5a451d60319f167440b775eda2044312557ffb9d17d1a5e392013475
expected_angle_gles1_sha=bfc3e5a2af81b4c2f173b35fd2bafaa871364a09652b5819410c0cda344871ce
expected_angle_gles2_sha=880df44fa7ffbc865242c86c18bbc97e4cb7dfceddb5a3c00d46924d4d5128c4
expected_angle_probe_sha=98e953797c7f909563a7eea404d0cb494f03303f601b3a84d649f4231e8774ed
expected_swangle_probe_sha=ea11580ec7c5fd882183592f10dde83a1489fe0b25d489a1737c65296a80f939
expected_pastel_sha=c3ca9451b80f4590c01175fa802a6bfcec9ee2c7cd08238ebb0abfa94ba1f100
expected_noop_composer_sha=e5c002c43532b16250908eb125b1a43f40aade6e783800f82744f6528b47c8ea
expected_noop_composer_manifest_sha=50d8adaa7d6cd31e4ddb40e51c7e403931b1a85ef205dc25f93b44a4273c9d4a
expected_stock_allocator_sha=1459a9fe7541a8b891f30c688c1f3dea4e8a350b893dbc923899978938d76a7f
expected_stock_allocator_rc_sha=5f1d3dbdd7c2ca4ffbcdfd54883f5d6f1b98fe0eed6f0f0c84f67644da828f78
expected_system_heap_module_sha=14b9f12b8977360e2da83c5d3bf892dc0f3ce9950e25e25bc75862de3e5e4994
expected_apex_inventory_sha=e012ae5a9ae5532adb0081fa8f18b22ddb7582e7a11955f917835fef891032bc
expected_apex_hash_manifest_sha=6aa8fd25e7ce45a23439cb0396119f71b4e247493c43b68fcefeee1a7f7ebbf5
expected_apex_partition_hashes_sha=4351d2c5d5ca74d43a1053507d795b102e7ef133ca79b028987d2d51b5dd1528
expected_permission_controller_sha=8d8ac0e1df5278598fd1bc293551f415a02a5522850ed67ba89263e5a405633a
expected_permission_controller_allowlist_sha=19be3efe65bea7b0b8f4ac7a3e362430503eafd7d9489590ce2869a18e0d16e8
expected_extservices_sha=7a875327b9cb2dbba4f4990c6a24b2ef84a5d2612224a6303957007e83e0f2c2
expected_extservices_allowlist_sha=5094bd9254ab442529a57712543dcbc505440961df5b5ea76be2e1424e538a87
expected_sdk_sandbox_sha=fec4230c2493ae774be2edf09fef92445d6092d47fbf4dc5581f6451751629a8
expected_connectivity_resources_sha=a02db6d13987c8d90e86b77ac97bee289103f7223812fd032847c8a2f7baf9d8
expected_stock_sensors_multihal_sha=44dfd54fd9d2125ac036a58d2f16cc4acc3981816b80b60b80f3cc38c2e9c8a6
expected_stock_netd_sha=79aa2dfd5ab601e562f631a83eb6e6171350275a333a5b0c31762d70a63da59a
expected_stock_netd_rc_sha=f3649dbb95b5e57b7543213a07079d1e900d5cdbcf498f28334a2981a940c96e
expected_stock_netbpfload_sha=a09af9bbf6bd99e20b6ab44e1762b55530531884dec11222144a9b3b2bb90531
expected_stock_netbpfload_rc_sha=b6eb65420c99148ad0c9ebc611d9733768d1d07085e52b8028502111ebbe58a3
expected_stock_netd_bpf_object_sha=c2254d2bc7e91f72018e44c71e332b320924e4699a5e4d618d3941a2584a70f3
expected_stock_clatd_bpf_object_sha=d23377355cdfc2767697cea2fba9defe24c4b421627d721e78e48f027a260907
expected_stock_dscp_policy_bpf_object_sha=b488bd2421312cf2546cf3d2d9164d768312a7a24b361e5b272e954d5d53cf51
expected_stock_service_connectivity_jar_sha=66f0403dd405f8322d758506074f5e6a5511733cab37482aa5929560c2feb28d
expected_stock_platform_bpfloader_sha=3b12618f9f9fac538cd19ce13743858e4efe3db24c8a2992142de8ed2cd23771
expected_stock_audioserver_sha=9033b26bb4433d71f753c6c7672a313640b8f3bb3b0f9207c0ac711014dfd068
expected_stock_audioserver_rc_sha=0007967a7c056469794143f459872805b34a7abd584b055036dbaf2048d64834
expected_stock_vendor_audio_hal_sha=39c8805285bc548e5f702cbf34aad1a9ce27ce1ca2c4c91c2510db34b9a8344f
expected_stock_vendor_audio_hal_rc_sha=97ab161b94f709f23969c71720f3e9606f7efc72d33f0d6d5638d0c95bd18499
expected_stock_default_audio_hal_sha=1ce63cd97b7068ac5b17ab2b970115ddb3b6da4cc43e9a04772c1e01cbf27a43
expected_stock_gpuservice_sha=c50f396476854212d2f046ac09e4f4a60b32f00df756a91b85dcf925ccd0f9f9
expected_stock_gpuservice_rc_sha=a4f71d84d4c830c08aae58a434727691fa972a40a2b5cb8e2288d2d80d42f7de
expected_netbpfload_adapter_sha=61719ecc20a62173ccfa77a20f9af7a4728dc15bf354316949e2e6be3f23ea5b
expected_connectivity_adapter_sha=0f0be823f759d39d8deb03ce7556a11f081b0b40bab955dbd414346668ef35a7
expected_service_connectivity_adapter_sha=510b49abdd7338901911c409802ef58469eeacce32e9683f10d37e954cb3ef9e
expected_nfnetlink_log_sha=0a13f1d1d466d0061e69cddb48405bca0d3636b469a2d12eac0603aacd39e298
expected_xt_nflog_sha=e553374a4843ab39cd3923bfd54422218fdcc12ca4946e5861adeb339f87553c
expected_xt_bpf_sha=0c204964a4c68073b10e9fc68a88eb72cea9b04b4f4d2cd4f6c36a453a2d3050
expected_xt_connmark_sha=948647ac50ca0f6e500644587b998952d773ded874adbae14ab5c31cc5a7e247
expected_xt_policy_sha=41940137bb8472d95299f84a99da6b69bf47659eb8e3e47a2ab955cece5c333a
expected_xt_state_sha=9798df7f778ad60c3effdc2867bc5bdc6489a9434d2314725207e3660f28bc91
expected_xt_u32_sha=5e1fefb3dcb8ee67bd746999b77ddb26ec0ecf3f2ca7a77971eafa258b1a753d
run_id=$(date -u +%Y%m%dT%H%M%SZ)
run_dir=$state/state/logs/stock-qseecomd-$run_id
config_backup=$run_dir/config.before
kernel_follow_pid=
logcat_pid=
container_started=false
module_loaded=false
override_created=false
apex_override_created=false
ssd_scratch_created=false
netbpfload_adapter_created=false
vendor_dmabuf_created=false
vendor_dmabuf_dir_created=false
system_mink_created=false
selinux_disabled_created=false
restorecon_disabled_created=false
angle_null_created=false
angle_probe_created=false
swangle_created=false
swangle_probe_created=false
noop_composer_created=false
permission_controller_target_created=false
permission_controller_allowlist_target_created=false
extservices_target_created=false
extservices_allowlist_target_created=false
sdk_sandbox_target_created=false
connectivity_resources_target_created=false
empty_sensors_config_created=false
system_heap_mode=
tee_mode=
frp_loop_node=
frp_loop_mode=
frp_loop_uid=
frp_loop_gid=
result=fail
cleanup_fault=none
binder_mode=
framework_required=${LUMA_C1_REQUIRE_FRAMEWORK:-false}
exact_apexd=${LUMA_C1_EXACT_APEXD:-false}
exact_surfaceflinger=${LUMA_C1_EXACT_SURFACEFLINGER:-false}
angle_null_renderer=${LUMA_C1_ANGLE_NULL_RENDERER:-false}
swangle_pastel_renderer=${LUMA_C1_SWANGLE_PASTEL_RENDERER:-false}
aosp_noop_composer=${LUMA_C1_AOSP_NOOP_COMPOSER:-false}
exact_stock_allocator=${LUMA_C1_EXACT_STOCK_ALLOCATOR:-false}
exact_apex_loop_metadata=${LUMA_C1_EXACT_APEX_LOOP_METADATA:-false}
permission_controller_projection=${LUMA_C1_PERMISSION_CONTROLLER_PROJECTION:-false}
empty_sensors_hal=${LUMA_C1_EMPTY_SENSORS_HAL:-false}
minimal_sensors_vintf=${LUMA_C1_MINIMAL_SENSORS_VINTF:-false}
minimal_wifi_vintf=${LUMA_C1_MINIMAL_WIFI_VINTF:-false}
isolated_netd=${LUMA_C1_ISOLATED_NETD:-false}
exact_audioserver=${LUMA_C1_EXACT_AUDIOSERVER:-false}
framework_stable_samples_required=${LUMA_C1_FRAMEWORK_STABLE_SAMPLES:-25}
declare -A binder_original_modes=()
declare -a apex_loop_nodes=()
declare -a apex_loop_majors=()
declare -a apex_loop_minors=()
declare -a apex_loop_backing_files=()
declare -a netfilter_new_modules=()
netfilter_gate_modules=(
  nfnetlink x_tables nf_defrag_ipv4 nf_defrag_ipv6 nf_conntrack nf_nat
  nf_reject_ipv4 nf_reject_ipv6
  ip_tables iptable_filter iptable_raw iptable_mangle iptable_nat
  ip6_tables ip6table_filter ip6table_raw ip6table_mangle ip6table_nat
  xt_tcpudp xt_mark xt_conntrack xt_TCPMSS ipt_REJECT ip6t_REJECT
)
netfilter_custom_modules=(
  nfnetlink_log xt_NFLOG xt_connmark xt_bpf xt_policy xt_state xt_u32
)

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

check_luma_primary_owners() {
  local service
  for service in ModemManager luma-fp6-cellular-link; do
    systemctl is-active --quiet "$service" || return 1
  done
}

record_luma_owners() {
  systemctl show ModemManager luma-fp6-cellular-link luma-fp6-imsd \
    -p Id -p ActiveState -p SubState -p Result -p InvocationID -p NRestarts \
    --no-pager
}

stop_logcat() {
  if [[ -n $logcat_pid ]]; then
    kill -TERM "$logcat_pid" 2>/dev/null || true
    wait "$logcat_pid" 2>/dev/null || true
    logcat_pid=
  fi
}

binder_proc_count() {
  find /sys/kernel/debug/binder/proc -mindepth 1 -maxdepth 1 -type f \
    2>/dev/null | wc -l
}

restore_legacy_binder_modes() {
  local binder_node
  for binder_node in "${!binder_original_modes[@]}"; do
    chmod "${binder_original_modes[$binder_node]}" "/dev/$binder_node" \
      2>/dev/null || cleanup_fault=legacy_binder_mode_restore_failed
    unset 'binder_original_modes[$binder_node]'
  done
}

cleanup() {
  local status=$?
  set +e
  stop_logcat
  if [[ $container_started == true ]]; then
    if [[ $angle_null_renderer == true || $swangle_pastel_renderer == true ]]; then
      lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/setprop persist.graphics.egl '' >/dev/null 2>&1 || true
    fi
    lxc-stop -P "$lxc_path" -n "$name" -k >/dev/null 2>&1 || true
    container_started=false
  fi
  if ((${#netfilter_new_modules[@]})); then
    local module_index module_name module_refs module_try
    for ((module_index=${#netfilter_new_modules[@]}-1; module_index>=0; module_index--)); do
      module_name=${netfilter_new_modules[$module_index]}
      for module_try in {1..20}; do
        if ! grep -qw "$module_name" /proc/modules; then
          break
        fi
        module_refs=$(awk -v name="$module_name" '$1 == name {print $3}' /proc/modules)
        if [[ ${module_refs:-x} == 0 ]] && rmmod "$module_name" 2>/dev/null; then
          break
        fi
        sleep 0.1
      done
      if grep -qw "$module_name" /proc/modules; then
        cleanup_fault=netfilter_module_cleanup_failed
      fi
    done
    netfilter_new_modules=()
  fi
  if [[ $isolated_netd == true && -f $run_dir/host-bpffs-before.log ]]; then
    find /sys/fs/bpf -xdev -mindepth 1 \
      -printf '%P type=%y mode=%m uid=%u gid=%g\n' 2>/dev/null | sort \
      >"$run_dir/host-bpffs-after.log"
    cmp -s "$run_dir/host-bpffs-before.log" "$run_dir/host-bpffs-after.log" ||
      cleanup_fault=host_bpffs_changed
  fi
  if [[ -f $config_backup ]]; then
    install -o root -g root -m 0600 "$config_backup" "$config"
  fi
  restore_legacy_binder_modes
  if [[ $override_created == true && -f $override ]]; then
    rm -f -- "$override"
    override_created=false
  fi
  if [[ $apex_override_created == true && -f $apex_override ]]; then
    rm -f -- "$apex_override"
    apex_override_created=false
  fi
  if [[ $permission_controller_target_created == true ]]; then
    rmdir -- "$permission_controller_target" 2>/dev/null ||
      cleanup_fault=permission_controller_projection_cleanup_failed
    permission_controller_target_created=false
  fi
  if [[ $extservices_target_created == true ]]; then
    rmdir -- "$extservices_target" 2>/dev/null ||
      cleanup_fault=extservices_projection_cleanup_failed
    extservices_target_created=false
  fi
  if [[ $sdk_sandbox_target_created == true ]]; then
    rmdir -- "$sdk_sandbox_target" 2>/dev/null ||
      cleanup_fault=sdk_sandbox_projection_cleanup_failed
    sdk_sandbox_target_created=false
  fi
  if [[ $connectivity_resources_target_created == true ]]; then
    rmdir -- "$connectivity_resources_target" 2>/dev/null ||
      cleanup_fault=connectivity_resources_projection_cleanup_failed
    connectivity_resources_target_created=false
  fi
  if [[ -n $frp_loop_node ]]; then
    if [[ -b $frp_loop_node ]]; then
      chmod "$frp_loop_mode" "$frp_loop_node" 2>/dev/null ||
        cleanup_fault=frp_loop_mode_restore_failed
      chown "$frp_loop_uid:$frp_loop_gid" "$frp_loop_node" 2>/dev/null ||
        cleanup_fault=frp_loop_owner_restore_failed
      losetup -d "$frp_loop_node" 2>/dev/null ||
        cleanup_fault=frp_loop_detach_failed
    else
      cleanup_fault=frp_loop_node_missing
    fi
    frp_loop_node=
  fi
  if [[ $empty_sensors_config_created == true ]]; then
    rm -f -- "$empty_sensors_config" ||
      cleanup_fault=empty_sensors_config_cleanup_failed
    empty_sensors_config_created=false
  fi
  if [[ $permission_controller_allowlist_target_created == true ]]; then
    rm -f -- "$permission_controller_allowlist_target" ||
      cleanup_fault=permission_controller_allowlist_cleanup_failed
    permission_controller_allowlist_target_created=false
  fi
  if [[ $extservices_allowlist_target_created == true ]]; then
    rm -f -- "$extservices_allowlist_target" ||
      cleanup_fault=extservices_allowlist_cleanup_failed
    extservices_allowlist_target_created=false
  fi
  if [[ $ssd_scratch_created == true ]]; then
    rm -f -- "$ssd_scratch/ssd" "$ssd_scratch/frp"
    rmdir -- "$ssd_scratch" 2>/dev/null || cleanup_fault=ssd_scratch_cleanup_failed
    ssd_scratch_created=false
  fi
  if [[ $netbpfload_adapter_created == true ]]; then
    rm -f -- "$netbpfload_adapter_target" ||
      cleanup_fault=netbpfload_adapter_cleanup_failed
    netbpfload_adapter_created=false
  fi
  if [[ $vendor_dmabuf_created == true ]]; then
    rm -f -- "$vendor_dmabuf_target" || cleanup_fault=vendor_dmabuf_cleanup_failed
    vendor_dmabuf_created=false
  fi
  if [[ $vendor_dmabuf_dir_created == true ]]; then
    rmdir -- "$vendor_dmabuf_dir" 2>/dev/null || cleanup_fault=vendor_dmabuf_dir_cleanup_failed
    vendor_dmabuf_dir_created=false
  fi
  if [[ $system_mink_created == true ]]; then
    rm -f -- "$system_mink_target" || cleanup_fault=system_mink_cleanup_failed
    system_mink_created=false
  fi
  if [[ $selinux_disabled_created == true ]]; then
    rm -f -- "$selinux_disabled_target" || cleanup_fault=selinux_shim_cleanup_failed
    selinux_disabled_created=false
  fi
  if [[ $restorecon_disabled_created == true ]]; then
    rm -f -- "$restorecon_disabled_target" ||
      cleanup_fault=restorecon_shim_cleanup_failed
    restorecon_disabled_created=false
  fi
  if [[ $angle_null_created == true ]]; then
    rm -f -- "$angle_egl_target" "$angle_gles1_target" "$angle_gles2_target" ||
      cleanup_fault=angle_null_cleanup_failed
    angle_null_created=false
  fi
  if [[ $angle_probe_created == true ]]; then
    rm -f -- "$angle_probe_target" || cleanup_fault=angle_probe_cleanup_failed
    angle_probe_created=false
  fi
  if [[ $swangle_created == true ]]; then
    rm -f -- "$swangle_egl_target" "$swangle_gles1_target" "$swangle_gles2_target" ||
      cleanup_fault=swangle_cleanup_failed
    swangle_created=false
  fi
  if [[ $swangle_probe_created == true ]]; then
    rm -f -- "$swangle_probe_target" || cleanup_fault=swangle_probe_cleanup_failed
    swangle_probe_created=false
  fi
  if [[ $noop_composer_created == true ]]; then
    rm -f -- "$noop_composer_target" || cleanup_fault=noop_composer_cleanup_failed
    for noop_lib in "${noop_composer_lib_names[@]}"; do
      rm -f -- "$noop_composer_lib_dir_target/$noop_lib" ||
        cleanup_fault=noop_composer_cleanup_failed
    done
    rmdir -- "$noop_composer_lib_dir_target" 2>/dev/null ||
      cleanup_fault=noop_composer_lib_dir_cleanup_failed
    noop_composer_created=false
  fi
  if [[ -n $system_heap_mode && -c /dev/dma_heap/system ]]; then
    chmod "$system_heap_mode" /dev/dma_heap/system 2>/dev/null ||
      cleanup_fault=system_heap_mode_restore_failed
    system_heap_mode=
  fi
  if [[ -f $adapter_diagnostic_log && -d $run_dir ]]; then
    install -o root -g root -m 0600 "$adapter_diagnostic_log" \
      "$run_dir/adapter-metadata.log" || cleanup_fault=adapter_log_copy_failed
    rm -f -- "$adapter_diagnostic_log" || cleanup_fault=adapter_log_cleanup_failed
  fi
  if [[ -n $tee_mode && -c /dev/tee0 ]]; then
    chmod "$tee_mode" /dev/tee0 2>/dev/null || true
    tee_mode=
  fi
  if [[ $module_loaded == true ]] && grep -qw qcomtee /proc/modules; then
    refs=$(awk '$1=="qcomtee" {print $3}' /proc/modules)
    if [[ ${refs:-x} != 0 ]]; then
      cleanup_fault=qcomtee_busy
    elif ! rmmod qcomtee 2>/dev/null; then
      cleanup_fault=qcomtee_unload_failed
    else
      module_loaded=false
    fi
  fi
  if [[ -n $kernel_follow_pid ]]; then
    kill "$kernel_follow_pid" 2>/dev/null || true
    wait "$kernel_follow_pid" 2>/dev/null || true
    kernel_follow_pid=
  fi
  if [[ $binder_mode == legacy-exclusive ]] &&
     [[ $(binder_proc_count) -ne 0 ]]; then
    cleanup_fault=legacy_binder_clients_remain
  fi
  if [[ -d $run_dir ]]; then
    record_luma_owners >"$run_dir/luma-owners-after.log" 2>&1 || true
    printf 'result=%s\ncleanup_fault=%s\ncontainer_started=%s\nmodule_loaded=%s\n' \
      "$result" "$cleanup_fault" "$container_started" "$module_loaded" \
      >"$run_dir/result.env"
    chmod 0600 "$run_dir"/* 2>/dev/null || true
  fi
  if [[ $cleanup_fault != none ]]; then
    printf 'error: cleanup fault: %s; reboot required before another secure gate\n' \
      "$cleanup_fault" >&2
    return 1
  fi
  return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || die 'run as root on the FP6'
[[ $framework_required == true || $framework_required == false ]] ||
  die 'LUMA_C1_REQUIRE_FRAMEWORK must be true or false'
[[ $exact_apexd == true || $exact_apexd == false ]] ||
  die 'LUMA_C1_EXACT_APEXD must be true or false'
[[ $exact_surfaceflinger == true || $exact_surfaceflinger == false ]] ||
  die 'LUMA_C1_EXACT_SURFACEFLINGER must be true or false'
[[ $angle_null_renderer == true || $angle_null_renderer == false ]] ||
  die 'LUMA_C1_ANGLE_NULL_RENDERER must be true or false'
[[ $swangle_pastel_renderer == true || $swangle_pastel_renderer == false ]] ||
  die 'LUMA_C1_SWANGLE_PASTEL_RENDERER must be true or false'
[[ $aosp_noop_composer == true || $aosp_noop_composer == false ]] ||
  die 'LUMA_C1_AOSP_NOOP_COMPOSER must be true or false'
[[ $exact_stock_allocator == true || $exact_stock_allocator == false ]] ||
  die 'LUMA_C1_EXACT_STOCK_ALLOCATOR must be true or false'
[[ $exact_apex_loop_metadata == true || $exact_apex_loop_metadata == false ]] ||
  die 'LUMA_C1_EXACT_APEX_LOOP_METADATA must be true or false'
[[ $permission_controller_projection == true || $permission_controller_projection == false ]] ||
  die 'LUMA_C1_PERMISSION_CONTROLLER_PROJECTION must be true or false'
[[ $empty_sensors_hal == true || $empty_sensors_hal == false ]] ||
  die 'LUMA_C1_EMPTY_SENSORS_HAL must be true or false'
[[ $minimal_sensors_vintf == true || $minimal_sensors_vintf == false ]] ||
  die 'LUMA_C1_MINIMAL_SENSORS_VINTF must be true or false'
[[ $minimal_wifi_vintf == true || $minimal_wifi_vintf == false ]] ||
  die 'LUMA_C1_MINIMAL_WIFI_VINTF must be true or false'
[[ $isolated_netd == true || $isolated_netd == false ]] ||
  die 'LUMA_C1_ISOLATED_NETD must be true or false'
[[ $exact_audioserver == true || $exact_audioserver == false ]] ||
  die 'LUMA_C1_EXACT_AUDIOSERVER must be true or false'
[[ $framework_stable_samples_required =~ ^[0-9]+$ ]] &&
  (( framework_stable_samples_required >= 25 && framework_stable_samples_required <= 1200 )) ||
  die 'LUMA_C1_FRAMEWORK_STABLE_SAMPLES must be an integer from 25 through 1200'
[[ $exact_apexd == false || $framework_required == true ]] ||
  die 'exact apexd mode requires the framework gate'
[[ $exact_surfaceflinger == false || $exact_apexd == true ]] ||
  die 'exact SurfaceFlinger mode requires exact apexd and the framework gate'
[[ $exact_apex_loop_metadata == false || $exact_apexd == true ]] ||
  die 'exact APEX loop metadata requires exact apexd'
[[ $permission_controller_projection == false || $exact_apex_loop_metadata == true ]] ||
  die 'permission-controller projection requires exact APEX loop metadata'
[[ $empty_sensors_hal == false || $framework_required == true ]] ||
  die 'empty sensors HAL requires the framework gate'
[[ $minimal_sensors_vintf == false || $framework_required == true ]] ||
  die 'minimal sensors VINTF requires the framework gate'
[[ $minimal_wifi_vintf == false || $framework_required == true ]] ||
  die 'minimal WiFi VINTF requires the framework gate'
[[ $minimal_sensors_vintf == false || $empty_sensors_hal == false ]] ||
  die 'minimal sensors VINTF and empty sensors HAL are mutually exclusive'
[[ $isolated_netd == false || $framework_required == true ]] ||
  die 'isolated netd requires the framework gate'
[[ $isolated_netd == false || $exact_apexd == true ]] ||
  die 'isolated netd requires the exact apexd lifecycle gate'
[[ $exact_audioserver == false || $framework_required == true ]] ||
  die 'exact audioserver requires the framework gate'
[[ $exact_audioserver == false || $exact_apexd == true ]] ||
  die 'exact audioserver requires the exact apexd lifecycle gate'
[[ $angle_null_renderer == false || $exact_surfaceflinger == true ]] ||
  die 'ANGLE null mode requires exact SurfaceFlinger'
[[ $angle_null_renderer == false || $swangle_pastel_renderer == false ]] ||
  die 'ANGLE null and SwANGLE/Pastel modes are mutually exclusive'
[[ $aosp_noop_composer == false || $exact_surfaceflinger == true ]] ||
  die 'AOSP no-op composer requires exact SurfaceFlinger'
[[ $aosp_noop_composer == false || $swangle_pastel_renderer == true ]] ||
  die 'AOSP no-op composer requires the SwANGLE/Pastel renderer'
[[ $exact_stock_allocator == false || $swangle_pastel_renderer == true ]] ||
  die 'exact stock allocator requires the SwANGLE/Pastel renderer'
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] ||
  die 'device identity mismatch'
[[ $(uname -r) == "$expected_kernel" ]] || die 'unexpected kernel release'
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || die 'device is not on slot b'
[[ $(lxc-info -P "$lxc_path" -n "$name" -sH 2>/dev/null || printf STOPPED) == STOPPED ]] ||
  die 'container is already active'
check_luma_primary_owners || die 'a primary Luma modem owner is unexpectedly inactive'
ims_state=$(systemctl show luma-fp6-imsd -p ActiveState --value)
[[ $ims_state == active || $ims_state == activating ]] ||
  die 'the pre-existing Luma IMS owner is neither active nor retrying'
! grep -qw qseecomtee /proc/modules || die 'legacy QSEECOM is loaded; clean reboot required'
! grep -qw qcomtee /proc/modules || die 'QCOMTEE is already loaded; clean reboot required'
! pgrep -x qsee-supplicant >/dev/null || die 'Linux qsee-supplicant is running'
[[ ! -e $override ]] || die 'temporary qseecomd override already exists'
[[ ! -e $apex_override ]] || die 'temporary apexd override already exists'
[[ ! -e $ssd_scratch ]] || die 'temporary SSD scratch already exists'
[[ ! -e $netbpfload_adapter_target && ! -L $netbpfload_adapter_target ]] ||
  die 'temporary netbpfload adapter target already exists'
[[ ! -e $empty_sensors_config && ! -L $empty_sensors_config ]] ||
  die 'temporary empty sensors configuration already exists'
[[ ! -e $vendor_dmabuf_target && ! -L $vendor_dmabuf_target ]] ||
  die 'vendor DMA-buffer compatibility target already exists'
[[ ! -e $system_mink_target && ! -L $system_mink_target ]] ||
  die 'system Mink compatibility target already exists'
[[ ! -e $selinux_disabled_target && ! -L $selinux_disabled_target ]] ||
  die 'system SELinux compatibility target already exists'
[[ ! -e $restorecon_disabled_target && ! -L $restorecon_disabled_target ]] ||
  die 'system restorecon compatibility target already exists'
[[ ! -e $adapter_diagnostic_log && ! -L $adapter_diagnostic_log ]] ||
  die 'adapter diagnostic log already exists'
if [[ $angle_null_renderer == true || $swangle_pastel_renderer == true ]]; then
  for target in "$angle_egl_target" "$angle_gles1_target" "$angle_gles2_target"; do
    [[ ! -e $target && ! -L $target ]] ||
      die "ANGLE null target already exists: $target"
  done
  [[ ! -e $angle_probe_target && ! -L $angle_probe_target ]] ||
    die 'ANGLE null probe target already exists'
fi

if zgrep -qx 'CONFIG_ANDROID_BINDERFS=y' /proc/config.gz; then
  binder_mode=binderfs-private
elif zgrep -qx 'CONFIG_ANDROID_BINDER_IPC=y' /proc/config.gz &&
     zgrep -qx 'CONFIG_ANDROID_BINDER_DEVICES="binder,hwbinder,vndbinder"' /proc/config.gz; then
  binder_mode=legacy-exclusive
  for binder_node in binder hwbinder vndbinder; do
    [[ -c /dev/$binder_node && ! -L /dev/$binder_node ]] ||
      die "legacy Binder node is invalid: $binder_node"
    ! fuser -s "/dev/$binder_node" 2>/dev/null ||
      die "legacy Binder node is already in use: $binder_node"
    [[ $(stat -c '%a' "/dev/$binder_node") == 600 ]] ||
      die "legacy Binder node mode differs: $binder_node"
  done
  [[ $(binder_proc_count) -eq 0 ]] || die 'legacy Binder has active clients'
else
  die 'neither private BinderFS nor an exclusive legacy Binder boundary is available'
fi

[[ -f $manifest && ! -L $manifest ]] || die 'manifest is absent or linked'
[[ -f $config && ! -L $config ]] || die 'LXC config is absent or linked'
[[ $(sha256sum "$manifest" | cut -d' ' -f1) == "$expected_manifest_sha" ]] ||
  die 'manifest hash mismatch'
[[ $(sha256sum "$config" | cut -d' ' -f1) == "$expected_config_sha" ]] ||
  die 'LXC config hash mismatch'
[[ -f $module && ! -L $module ]] || die 'QCOMTEE module is absent or linked'
[[ $(sha256sum "$module" | cut -d' ' -f1) == "$expected_module_sha" ]] ||
  die 'QCOMTEE module hash mismatch'
[[ $(modinfo -F name "$module") == qcomtee ]] || die 'QCOMTEE module identity mismatch'
[[ $(modinfo -F vermagic "$module" | awk '{print $1}') == "$expected_kernel" ]] ||
  die 'QCOMTEE module vermagic mismatch'
[[ -z $(modinfo -F depends "$module") ]] || die 'QCOMTEE module dependency mismatch'

vendor=$state/mounts/vendor
stock_qseecomd=$vendor/bin/qseecomd
stock_qseecomd_rc=$vendor/etc/init/qseecomd.rc
[[ $(sha256sum "$stock_qseecomd" | cut -d' ' -f1) == "$expected_qseecomd_sha" ]] ||
  die 'stock qseecomd hash mismatch'
[[ $(sha256sum "$stock_qseecomd_rc" | cut -d' ' -f1) == "$expected_qseecomd_rc_sha" ]] ||
  die 'stock qseecomd rc hash mismatch'
[[ $(sha256sum "$vendor/lib64/libQSEEComAPI.so" | cut -d' ' -f1) == "$expected_qseecom_api_sha" ]] ||
  die 'stock libQSEEComAPI hash mismatch'
[[ $(sha256sum "$vendor/lib64/librpmb.so" | cut -d' ' -f1) == "$expected_rpmb_sha" ]] ||
  die 'stock librpmb hash mismatch'
[[ $(sha256sum "$vendor/lib64/libssd.so" | cut -d' ' -f1) == "$expected_ssd_sha" ]] ||
  die 'stock libssd hash mismatch'
[[ $(sha256sum "$vendor/lib64/libdrmtime.so" | cut -d' ' -f1) == "$expected_drmtime_sha" ]] ||
  die 'stock libdrmtime hash mismatch'
[[ -f $qseecomd_mink_adapter && ! -L $qseecomd_mink_adapter ]] ||
  die 'qseecomd Mink adapter is absent or linked'
[[ $(sha256sum "$qseecomd_mink_adapter" | cut -d' ' -f1) == "$expected_mink_adapter_sha" ]] ||
  die 'Mink adapter hash mismatch'
! readelf -Ws "$qseecomd_mink_adapter" |
  grep -Eq '__aarch64_(cas|ldadd)' ||
  die 'Mink adapter contains unsupported outlined atomic helpers'
[[ -f $qseecomd_dmabuf_adapter && ! -L $qseecomd_dmabuf_adapter ]] ||
  die 'qseecomd DMA-buffer adapter is absent or linked'
[[ $(sha256sum "$qseecomd_dmabuf_adapter" | cut -d' ' -f1) == "$expected_dmabuf_adapter_sha" ]] ||
  die 'DMA-buffer adapter hash mismatch'
if [[ $framework_required == true ]]; then
  [[ ! -e /sys/fs/selinux/enforce ]] ||
    die 'framework compatibility gate requires the host SELinux filesystem to be absent'
  [[ -f $selinux_disabled_shim && ! -L $selinux_disabled_shim ]] ||
    die 'SELinux-disabled compatibility shim is absent or linked'
  [[ $(sha256sum "$selinux_disabled_shim" | cut -d' ' -f1) == "$expected_selinux_disabled_shim_sha" ]] ||
    die 'SELinux-disabled compatibility shim hash mismatch'
  [[ -f $restorecon_disabled_shim && ! -L $restorecon_disabled_shim ]] ||
    die 'restorecon-disabled compatibility hook is absent or linked'
  [[ $(sha256sum "$restorecon_disabled_shim" | cut -d' ' -f1) == \
     "$expected_restorecon_disabled_shim_sha" ]] ||
    die 'restorecon-disabled compatibility hook hash mismatch'
fi
if [[ $exact_audioserver == true ]]; then
  [[ -f $stock_audioserver && ! -L $stock_audioserver ]] ||
    die 'exact stock audioserver is absent or linked'
  [[ -f $stock_audioserver_rc && ! -L $stock_audioserver_rc ]] ||
    die 'exact stock audioserver rc is absent or linked'
  [[ $(sha256sum "$stock_audioserver" | cut -d' ' -f1) == \
     "$expected_stock_audioserver_sha" ]] ||
    die 'exact stock audioserver hash mismatch'
  [[ $(sha256sum "$stock_audioserver_rc" | cut -d' ' -f1) == \
     "$expected_stock_audioserver_rc_sha" ]] ||
    die 'exact stock audioserver rc hash mismatch'
  [[ -f $stock_vendor_audio_hal && ! -L $stock_vendor_audio_hal ]] ||
    die 'exact stock vendor audio HAL is absent or linked'
  [[ -f $stock_vendor_audio_hal_rc && ! -L $stock_vendor_audio_hal_rc ]] ||
    die 'exact stock vendor audio HAL rc is absent or linked'
  [[ $(sha256sum "$stock_vendor_audio_hal" | cut -d' ' -f1) == \
     "$expected_stock_vendor_audio_hal_sha" ]] ||
    die 'exact stock vendor audio HAL hash mismatch'
  [[ $(sha256sum "$stock_vendor_audio_hal_rc" | cut -d' ' -f1) == \
     "$expected_stock_vendor_audio_hal_rc_sha" ]] ||
    die 'exact stock vendor audio HAL rc hash mismatch'
  [[ -f $stock_default_audio_hal && ! -L $stock_default_audio_hal ]] ||
    die 'exact stock default audio HAL is absent or linked'
  [[ $(sha256sum "$stock_default_audio_hal" | cut -d' ' -f1) == \
     "$expected_stock_default_audio_hal_sha" ]] ||
    die 'exact stock default audio HAL hash mismatch'
  [[ -f $framework_audio_manifest && ! -L $framework_audio_manifest ]] ||
    die 'framework audio VINTF projection is absent or linked'
  [[ $(sha256sum "$framework_audio_manifest" | cut -d' ' -f1) == \
     "$expected_framework_audio_manifest_sha" ]] ||
    die 'framework audio VINTF projection hash mismatch'
  [[ -f $stock_non_qmaa_manifest && ! -L $stock_non_qmaa_manifest ]] ||
    die 'exact stock non-QMAA manifest is absent or linked'
  [[ $(sha256sum "$stock_non_qmaa_manifest" | cut -d' ' -f1) == \
     "$expected_stock_non_qmaa_manifest_sha" ]] ||
    die 'exact stock non-QMAA manifest hash mismatch'
  [[ -f $stock_gpuservice && ! -L $stock_gpuservice ]] ||
    die 'exact stock gpuservice is absent or linked'
  [[ -f $stock_gpuservice_rc && ! -L $stock_gpuservice_rc ]] ||
    die 'exact stock gpuservice rc is absent or linked'
  [[ $(sha256sum "$stock_gpuservice" | cut -d' ' -f1) == \
     "$expected_stock_gpuservice_sha" ]] ||
    die 'exact stock gpuservice hash mismatch'
  [[ $(sha256sum "$stock_gpuservice_rc" | cut -d' ' -f1) == \
     "$expected_stock_gpuservice_rc_sha" ]] ||
    die 'exact stock gpuservice rc hash mismatch'
fi
if [[ $empty_sensors_hal == true ]]; then
  [[ -f $stock_sensors_multihal && ! -L $stock_sensors_multihal ]] ||
    die 'exact stock AOSP sensors Multi-HAL is absent or linked'
  [[ $(sha256sum "$stock_sensors_multihal" | cut -d' ' -f1) == \
     "$expected_stock_sensors_multihal_sha" ]] ||
    die 'exact stock AOSP sensors Multi-HAL hash mismatch'
fi
if [[ $minimal_sensors_vintf == true ]]; then
  [[ -f $minimal_sensors_manifest && ! -L $minimal_sensors_manifest ]] ||
    die 'minimal sensors manifest is absent or linked'
  [[ $(sha256sum "$minimal_sensors_manifest" | cut -d' ' -f1) == \
     "$expected_minimal_sensors_manifest_sha" ]] ||
    die 'minimal sensors manifest hash mismatch'
fi
if [[ $minimal_wifi_vintf == true ]]; then
  [[ -f $minimal_empty_permissions && ! -L $minimal_empty_permissions ]] ||
    die 'minimal empty permissions projection is absent or linked'
  [[ $(sha256sum "$minimal_empty_permissions" | cut -d' ' -f1) == \
     "$expected_minimal_empty_permissions_sha" ]] ||
    die 'minimal empty permissions projection hash mismatch'
  [[ -f $minimal_sensors_manifest && ! -L $minimal_sensors_manifest ]] ||
    die 'minimal empty device manifest is absent or linked'
  [[ $(sha256sum "$minimal_sensors_manifest" | cut -d' ' -f1) == \
     "$expected_minimal_sensors_manifest_sha" ]] ||
    die 'minimal empty device manifest hash mismatch'
fi
if [[ $isolated_netd == true ]]; then
  [[ -f $stock_netd && ! -L $stock_netd ]] ||
    die 'exact stock netd is absent or linked'
  [[ $(sha256sum "$stock_netd" | cut -d' ' -f1) == "$expected_stock_netd_sha" ]] ||
    die 'exact stock netd hash mismatch'
  [[ -f $stock_netd_rc && ! -L $stock_netd_rc ]] ||
    die 'exact stock netd rc is absent or linked'
  [[ $(sha256sum "$stock_netd_rc" | cut -d' ' -f1) == "$expected_stock_netd_rc_sha" ]] ||
    die 'exact stock netd rc hash mismatch'
  [[ -f $stock_netbpfload && ! -L $stock_netbpfload ]] ||
    die 'exact stock mainline netbpfload is absent or linked'
  [[ $(sha256sum "$stock_netbpfload" | cut -d' ' -f1) == \
     "$expected_stock_netbpfload_sha" ]] ||
    die 'exact stock mainline netbpfload hash mismatch'
  [[ -f $stock_netbpfload_rc && ! -L $stock_netbpfload_rc ]] ||
    die 'exact stock mainline netbpfload rc is absent or linked'
  [[ $(sha256sum "$stock_netbpfload_rc" | cut -d' ' -f1) == \
     "$expected_stock_netbpfload_rc_sha" ]] ||
    die 'exact stock mainline netbpfload rc hash mismatch'
  [[ -f $stock_netd_bpf_object && ! -L $stock_netd_bpf_object ]] ||
    die 'exact stock netd BPF object is absent or linked'
  [[ $(sha256sum "$stock_netd_bpf_object" | cut -d' ' -f1) == \
     "$expected_stock_netd_bpf_object_sha" ]] ||
    die 'exact stock netd BPF object hash mismatch'
  [[ -f $stock_clatd_bpf_object && ! -L $stock_clatd_bpf_object ]] ||
    die 'exact stock CLAT BPF object is absent or linked'
  [[ $(sha256sum "$stock_clatd_bpf_object" | cut -d' ' -f1) == \
     "$expected_stock_clatd_bpf_object_sha" ]] ||
    die 'exact stock CLAT BPF object hash mismatch'
  [[ -f $stock_dscp_policy_bpf_object && ! -L $stock_dscp_policy_bpf_object ]] ||
    die 'exact stock DSCP policy BPF object is absent or linked'
  [[ $(sha256sum "$stock_dscp_policy_bpf_object" | cut -d' ' -f1) == \
     "$expected_stock_dscp_policy_bpf_object_sha" ]] ||
    die 'exact stock DSCP policy BPF object hash mismatch'
  [[ -f $stock_service_connectivity_jar && ! -L $stock_service_connectivity_jar ]] ||
    die 'exact stock service-connectivity JAR is absent or linked'
  [[ $(sha256sum "$stock_service_connectivity_jar" | cut -d' ' -f1) == \
     "$expected_stock_service_connectivity_jar_sha" ]] ||
    die 'exact stock service-connectivity JAR hash mismatch'
  [[ -f $stock_platform_bpfloader && ! -L $stock_platform_bpfloader ]] ||
    die 'exact stock platform bpfloader is absent or linked'
  [[ $(sha256sum "$stock_platform_bpfloader" | cut -d' ' -f1) == \
     "$expected_stock_platform_bpfloader_sha" ]] ||
    die 'exact stock platform bpfloader hash mismatch'
  [[ -f $netbpfload_adapter_source && ! -L $netbpfload_adapter_source ]] ||
    die 'isolated netbpfload adapter is absent or linked'
  [[ $(sha256sum "$netbpfload_adapter_source" | cut -d' ' -f1) == \
     "$expected_netbpfload_adapter_sha" ]] ||
    die 'isolated netbpfload adapter hash mismatch'
  [[ -f $connectivity_adapter_source && ! -L $connectivity_adapter_source ]] ||
    die 'container connectivity adaptation is absent or linked'
  [[ $(sha256sum "$connectivity_adapter_source" | cut -d' ' -f1) == \
     "$expected_connectivity_adapter_sha" ]] ||
    die 'container connectivity adaptation hash mismatch'
  [[ -f $service_connectivity_adapter_source && ! -L $service_connectivity_adapter_source ]] ||
    die 'container service-connectivity JAR adaptation is absent or linked'
  [[ $(sha256sum "$service_connectivity_adapter_source" | cut -d' ' -f1) == \
     "$expected_service_connectivity_adapter_sha" ]] ||
    die 'container service-connectivity JAR adaptation hash mismatch'
  [[ -d $netfilter_custom_module_dir && ! -L $netfilter_custom_module_dir ]] ||
    die 'isolated netfilter module directory is absent or linked'
  for module_record in \
    "nfnetlink_log:$expected_nfnetlink_log_sha" \
    "xt_NFLOG:$expected_xt_nflog_sha" \
    "xt_bpf:$expected_xt_bpf_sha" \
    "xt_connmark:$expected_xt_connmark_sha" \
    "xt_policy:$expected_xt_policy_sha" \
    "xt_state:$expected_xt_state_sha" \
    "xt_u32:$expected_xt_u32_sha"; do
    module_name=${module_record%%:*}
    module_sha=${module_record#*:}
    module_path=$netfilter_custom_module_dir/$module_name.ko
    [[ -f $module_path && ! -L $module_path ]] ||
      die "isolated netfilter module is absent or linked: $module_name"
    [[ $(sha256sum "$module_path" | cut -d' ' -f1) == "$module_sha" ]] ||
      die "isolated netfilter module hash mismatch: $module_name"
    [[ $(modinfo -F name "$module_path") == "$module_name" ]] ||
      die "isolated netfilter module identity mismatch: $module_name"
    [[ $(modinfo -F vermagic "$module_path" | awk '{print $1}') == "$expected_kernel" ]] ||
      die "isolated netfilter module vermagic mismatch: $module_name"
  done
  grep -qx 'lxc.net.0.type = empty' "$config" ||
    die 'isolated netd requires the empty LXC network boundary'
  [[ $(grep -c '^lxc\.net\.' "$config") -eq 1 ]] ||
    die 'isolated netd found an unexpected additional LXC network directive'
  ! grep -Eq 'sys/fs/bpf|[[:space:]]bpf[[:space:]]+bpf' "$config" ||
    die 'base C1 config already exposes a BPF filesystem'
fi
if [[ $exact_surfaceflinger == true ]]; then
  stock_surfaceflinger=$state/mounts/system/system/bin/surfaceflinger
  stock_surfaceflinger_rc=$state/mounts/system/system/etc/init/surfaceflinger.rc
  [[ -f $stock_surfaceflinger && ! -L $stock_surfaceflinger ]] ||
    die 'stock SurfaceFlinger is absent or linked'
  [[ $(sha256sum "$stock_surfaceflinger" | cut -d' ' -f1) == "$expected_surfaceflinger_sha" ]] ||
    die 'stock SurfaceFlinger hash mismatch'
  [[ $(sha256sum "$stock_surfaceflinger_rc" | cut -d' ' -f1) == "$expected_surfaceflinger_rc_sha" ]] ||
    die 'stock SurfaceFlinger rc hash mismatch'
fi
if [[ $angle_null_renderer == true ]]; then
  [[ $(sha256sum "$angle_egl_source" | cut -d' ' -f1) == "$expected_angle_egl_sha" ]] ||
    die 'stock ANGLE EGL hash mismatch'
  [[ $(sha256sum "$angle_gles1_source" | cut -d' ' -f1) == "$expected_angle_gles1_sha" ]] ||
    die 'stock ANGLE GLES1 hash mismatch'
  [[ $(sha256sum "$angle_gles2_source" | cut -d' ' -f1) == "$expected_angle_gles2_sha" ]] ||
    die 'stock ANGLE GLES2 hash mismatch'
  strings "$angle_gles2_source" | grep -F 'EGL_ANGLE_platform_angle_null' >/dev/null ||
    die 'stock ANGLE build lacks the null platform'
  strings "$angle_gles2_source" | grep -F 'ANGLE_DEFAULT_PLATFORM' >/dev/null ||
    die 'stock ANGLE build lacks environment backend selection'
  [[ -f $angle_probe_source && ! -L $angle_probe_source ]] ||
    die 'ANGLE null probe source is absent or linked'
  [[ $(sha256sum "$angle_probe_source" | cut -d' ' -f1) == "$expected_angle_probe_sha" ]] ||
    die 'ANGLE null probe hash mismatch'
fi
if [[ $swangle_pastel_renderer == true ]]; then
  [[ $(sha256sum "$angle_egl_source" | cut -d' ' -f1) == "$expected_angle_egl_sha" ]] ||
    die 'stock ANGLE EGL hash mismatch'
  [[ $(sha256sum "$angle_gles1_source" | cut -d' ' -f1) == "$expected_angle_gles1_sha" ]] ||
    die 'stock ANGLE GLES1 hash mismatch'
  [[ $(sha256sum "$angle_gles2_source" | cut -d' ' -f1) == "$expected_angle_gles2_sha" ]] ||
    die 'stock ANGLE GLES2 hash mismatch'
  [[ $(sha256sum "$pastel_source" | cut -d' ' -f1) == "$expected_pastel_sha" ]] ||
    die 'stock CPU-only Vulkan Pastel hash mismatch'
  [[ -f $swangle_probe_source && ! -L $swangle_probe_source ]] ||
    die 'SwANGLE/Pastel probe source is absent or linked'
  [[ $(sha256sum "$swangle_probe_source" | cut -d' ' -f1) == "$expected_swangle_probe_sha" ]] ||
    die 'SwANGLE/Pastel probe hash mismatch'
fi
if [[ $exact_stock_allocator == true ]]; then
  [[ $(sha256sum "$stock_allocator" | cut -d' ' -f1) == "$expected_stock_allocator_sha" ]] ||
    die 'stock graphics allocator hash mismatch'
  [[ $(sha256sum "$stock_allocator_rc" | cut -d' ' -f1) == "$expected_stock_allocator_rc_sha" ]] ||
    die 'stock graphics allocator rc hash mismatch'
  [[ -f $system_heap_module && ! -L $system_heap_module ]] ||
    die 'RAM-only AOSP system heap module is absent or linked'
  [[ $(sha256sum "$system_heap_module" | cut -d' ' -f1) == \
     "$expected_system_heap_module_sha" ]] ||
    die 'RAM-only AOSP system heap module hash mismatch'
  grep -qw luma_dma_heap_system /proc/modules ||
    die 'RAM-only AOSP system heap module is not loaded'
  [[ -c /dev/dma_heap/system ]] || die 'AOSP system DMA-BUF heap node is absent'
fi
if [[ $aosp_noop_composer == true ]]; then
  [[ -d $noop_composer_source && ! -L $noop_composer_source ]] ||
    die 'AOSP no-op composer payload is absent or linked'
  [[ $(sha256sum "$noop_composer_source/SHA256SUMS" | cut -d' ' -f1) == \
     "$expected_noop_composer_manifest_sha" ]] ||
    die 'AOSP no-op composer manifest hash mismatch'
  (cd "$noop_composer_source" && sha256sum -c SHA256SUMS >/dev/null) ||
    die 'AOSP no-op composer payload hash mismatch'
  [[ $(sha256sum "$noop_composer_source/bin/hw/android.hardware.graphics.composer3-service.ranchu" |
       cut -d' ' -f1) == "$expected_noop_composer_sha" ]] ||
    die 'AOSP no-op composer binary hash mismatch'
  [[ ! -e $noop_composer_target ]] ||
    die 'AOSP no-op composer target unexpectedly exists'
  [[ ! -e $noop_composer_lib_dir_target && ! -L $noop_composer_lib_dir_target ]] ||
    die 'AOSP no-op composer isolated library directory unexpectedly exists'
  for noop_lib in "${noop_composer_lib_names[@]}"; do
    [[ -f $noop_composer_source/lib64/$noop_lib &&
       ! -L $noop_composer_source/lib64/$noop_lib ]] ||
      die "AOSP no-op composer source library is absent or linked: $noop_lib"
  done
fi
if [[ $exact_apex_loop_metadata == true ]]; then
  [[ -d $apex_runtime/images && ! -L $apex_runtime/images ]] ||
    die 'verified APEX image directory is absent or linked'
  [[ -f $apex_inventory && ! -L $apex_inventory ]] ||
    die 'activated APEX inventory is absent or linked'
  [[ -f $apex_hash_manifest && ! -L $apex_hash_manifest ]] ||
    die 'APEX hash manifest is absent or linked'
  [[ -f $apex_partition_hashes && ! -L $apex_partition_hashes ]] ||
    die 'APEX partition hash inventory is absent or linked'
  [[ $(sha256sum "$apex_inventory" | cut -d' ' -f1) == \
     "$expected_apex_inventory_sha" ]] ||
    die 'activated APEX inventory hash mismatch'
  [[ $(sha256sum "$apex_hash_manifest" | cut -d' ' -f1) == \
     "$expected_apex_hash_manifest_sha" ]] ||
    die 'APEX hash-manifest hash mismatch'
  [[ $(sha256sum "$apex_partition_hashes" | cut -d' ' -f1) == \
     "$expected_apex_partition_hashes_sha" ]] ||
    die 'APEX partition-hash inventory mismatch'
  sha256sum -c "$apex_hash_manifest" >/dev/null ||
    die 'verified APEX runtime payload hash mismatch'

  if [[ $permission_controller_projection == true ]]; then
    [[ -d $permission_controller_source_dir && ! -L $permission_controller_source_dir ]] ||
      die 'exact PermissionController source directory is absent or linked'
    [[ -f $permission_controller_source && ! -L $permission_controller_source ]] ||
      die 'exact PermissionController APK is absent or linked'
    [[ $(sha256sum "$permission_controller_source" | cut -d' ' -f1) == \
       "$expected_permission_controller_sha" ]] ||
      die 'exact PermissionController APK hash mismatch'
    [[ -f $permission_controller_allowlist_source && ! -L $permission_controller_allowlist_source ]] ||
      die 'exact PermissionController allowlist is absent or linked'
    [[ $(sha256sum "$permission_controller_allowlist_source" | cut -d' ' -f1) == \
       "$expected_permission_controller_allowlist_sha" ]] ||
      die 'exact PermissionController allowlist hash mismatch'
    [[ -d $extservices_source_dir && ! -L $extservices_source_dir ]] ||
      die 'exact Services Extension source directory is absent or linked'
    [[ -f $extservices_source && ! -L $extservices_source ]] ||
      die 'exact Services Extension APK is absent or linked'
    [[ $(sha256sum "$extservices_source" | cut -d' ' -f1) == \
       "$expected_extservices_sha" ]] ||
      die 'exact Services Extension APK hash mismatch'
    [[ -f $extservices_allowlist_source && ! -L $extservices_allowlist_source ]] ||
      die 'exact Services Extension allowlist is absent or linked'
    [[ $(sha256sum "$extservices_allowlist_source" | cut -d' ' -f1) == \
       "$expected_extservices_allowlist_sha" ]] ||
      die 'exact Services Extension allowlist hash mismatch'
    [[ -d $sdk_sandbox_source_dir && ! -L $sdk_sandbox_source_dir ]] ||
      die 'exact SDK Sandbox source directory is absent or linked'
    [[ -f $sdk_sandbox_source && ! -L $sdk_sandbox_source ]] ||
      die 'exact SDK Sandbox APK is absent or linked'
    [[ $(sha256sum "$sdk_sandbox_source" | cut -d' ' -f1) == \
       "$expected_sdk_sandbox_sha" ]] ||
      die 'exact SDK Sandbox APK hash mismatch'
    [[ ! -e $permission_controller_target && ! -L $permission_controller_target ]] ||
      die 'PermissionController projection target unexpectedly exists'
    [[ ! -e $permission_controller_allowlist_target && ! -L $permission_controller_allowlist_target ]] ||
      die 'PermissionController allowlist target unexpectedly exists'
    [[ ! -e $extservices_target && ! -L $extservices_target ]] ||
      die 'Services Extension projection target unexpectedly exists'
    [[ ! -e $extservices_allowlist_target && ! -L $extservices_allowlist_target ]] ||
      die 'Services Extension allowlist target unexpectedly exists'
    [[ ! -e $sdk_sandbox_target && ! -L $sdk_sandbox_target ]] ||
      die 'SDK Sandbox projection target unexpectedly exists'
    [[ ! -e $connectivity_resources_target && ! -L $connectivity_resources_target ]] ||
      die 'connectivity resources projection target unexpectedly exists'
  fi

  # Exact apexd reconstructs its active database from /proc/mounts. Give it
  # read-only visibility of only the already-mounted, hash-verified APEX loop
  # devices and their immutable backing-file path. Never expose loop-control,
  # device-mapper control, a writable block permission, or a physical block
  # device.
  while IFS=$'\t' read -r apex_mountpoint apex_expected_image; do
    [[ $apex_mountpoint == "$apex_runtime/mounts/"* ]] ||
      die "APEX mountpoint escaped verified runtime: $apex_mountpoint"
    [[ $apex_expected_image == "$apex_runtime/images/"*.apex ]] ||
      die "APEX archive escaped verified runtime: $apex_expected_image"
    apex_loop_node=$(findmnt -n -o SOURCE --target "$apex_mountpoint")
    [[ $apex_loop_node =~ ^/dev/loop[0-9]+$ && -b $apex_loop_node ]] ||
      die "APEX mount is not backed by a loop block device: $apex_mountpoint"
    apex_loop_name=${apex_loop_node#/dev/}
    [[ $(cat "/sys/class/block/$apex_loop_name/ro") == 1 ]] ||
      die "APEX loop device is not kernel read-only: $apex_loop_node"
    apex_backing_file=$(cat "/sys/class/block/$apex_loop_name/loop/backing_file")
    [[ $apex_backing_file == "$apex_expected_image" ]] ||
      die "APEX loop backing file differs: $apex_loop_node"
    [[ -f $apex_backing_file && ! -L $apex_backing_file ]] ||
      die "APEX loop backing archive is absent or linked: $apex_backing_file"
    apex_loop_nodes+=("$apex_loop_node")
    apex_loop_majors+=("$((16#$(stat -c '%t' "$apex_loop_node")))")
    apex_loop_minors+=("$((16#$(stat -c '%T' "$apex_loop_node")))")
    apex_loop_backing_files+=("$apex_backing_file")
  done < <(python3 - "$apex_inventory" <<'PY'
import json
import sys
from pathlib import Path

for item in sorted(json.loads(Path(sys.argv[1]).read_text()), key=lambda x: x["mountpoint"]):
    print(f"{item['mountpoint']}\t{item['runtime_apex']}")
PY
  )
  [[ ${#apex_loop_nodes[@]} -eq 41 ]] ||
    die 'verified APEX loop-device count differs'
  [[ $(printf '%s\n' "${apex_loop_nodes[@]}" | sort -u | wc -l) -eq 41 ]] ||
    die 'an APEX loop device is unexpectedly shared'
fi
for required_dmabuf_symbol in \
  DmabufHeapCpuSyncStart DmabufHeapCpuSyncEnd \
  _ZN15BufferAllocatorC1Ev \
  _ZN15BufferAllocator5AllocERKNSt3__112basic_stringIcNS0_11char_traitsIcEENS0_9allocatorIcEEEEmjm; do
  readelf -Ws "$qseecomd_dmabuf_adapter" |
    awk -v symbol="$required_dmabuf_symbol" '$7 != "UND" && $8 == symbol { found=1 } END { exit !found }' ||
    die "DMA-buffer adapter is missing required symbol: $required_dmabuf_symbol"
done

install -d -o root -g root -m 0700 "$run_dir"
cp --preserve=mode,ownership,timestamps "$config" "$config_backup"
sha256sum "$manifest" "$config" "$module" "$stock_qseecomd" \
  "$stock_qseecomd_rc" "$vendor/lib64/libQSEEComAPI.so" \
  "$vendor/lib64/librpmb.so" "$vendor/lib64/libssd.so" \
  "$vendor/lib64/libdrmtime.so" "$qseecomd_mink_adapter" \
  "$qseecomd_dmabuf_adapter" >"$run_dir/input-hashes.log"
if [[ $framework_required == true ]]; then
  sha256sum "$selinux_disabled_shim" "$restorecon_disabled_shim" \
    >>"$run_dir/input-hashes.log"
fi
if [[ $exact_audioserver == true ]]; then
  sha256sum "$stock_audioserver" "$stock_audioserver_rc" \
    "$stock_vendor_audio_hal" "$stock_vendor_audio_hal_rc" \
    "$stock_default_audio_hal" \
    "$framework_audio_manifest" "$stock_non_qmaa_manifest" \
    "$stock_gpuservice" "$stock_gpuservice_rc" \
    >>"$run_dir/input-hashes.log"
fi
if [[ $exact_surfaceflinger == true ]]; then
  sha256sum "$stock_surfaceflinger" "$stock_surfaceflinger_rc" \
    >>"$run_dir/input-hashes.log"
fi
if [[ $swangle_pastel_renderer == true ]]; then
  sha256sum "$angle_egl_source" "$angle_gles1_source" "$angle_gles2_source" \
    "$pastel_source" "$swangle_probe_source" \
    >>"$run_dir/input-hashes.log"
fi
if [[ $aosp_noop_composer == true ]]; then
  (cd "$noop_composer_source" && sha256sum SHA256SUMS \
    bin/hw/android.hardware.graphics.composer3-service.ranchu lib64/*.so) \
    >>"$run_dir/input-hashes.log"
fi
if [[ $exact_stock_allocator == true ]]; then
  sha256sum "$stock_allocator" "$stock_allocator_rc" "$system_heap_module" \
    >>"$run_dir/input-hashes.log"
fi
if [[ $exact_apex_loop_metadata == true ]]; then
  sha256sum "$apex_inventory" "$apex_hash_manifest" "$apex_partition_hashes" \
    >>"$run_dir/input-hashes.log"
  (cd / && sha256sum -c "$apex_hash_manifest") \
    >"$run_dir/apex-runtime-hash-verification.log"
  : >"$run_dir/apex-loop-boundary.log"
  for apex_index in "${!apex_loop_nodes[@]}"; do
    printf 'node=%s major_minor=%s:%s kernel_ro=1 backing_file=%s\n' \
      "${apex_loop_nodes[$apex_index]}" \
      "${apex_loop_majors[$apex_index]}" \
      "${apex_loop_minors[$apex_index]}" \
      "${apex_loop_backing_files[$apex_index]}" \
      >>"$run_dir/apex-loop-boundary.log"
  done
fi
if [[ $permission_controller_projection == true ]]; then
  [[ -f $connectivity_resources_source && ! -L $connectivity_resources_source ]] ||
    die 'exact stock connectivity resources APK is absent or linked'
  [[ $(sha256sum "$connectivity_resources_source" | cut -d' ' -f1) == \
     "$expected_connectivity_resources_sha" ]] ||
    die 'exact stock connectivity resources APK hash mismatch'
  sha256sum "$permission_controller_source" \
    "$permission_controller_allowlist_source" \
    "$extservices_source" "$extservices_allowlist_source" \
    "$sdk_sandbox_source" "$connectivity_resources_source" \
    >>"$run_dir/input-hashes.log"
fi
if [[ $empty_sensors_hal == true ]]; then
  sha256sum "$stock_sensors_multihal" >>"$run_dir/input-hashes.log"
fi
if [[ $minimal_sensors_vintf == true ]]; then
  sha256sum "$minimal_sensors_manifest" >>"$run_dir/input-hashes.log"
fi
if [[ $minimal_wifi_vintf == true ]]; then
  sha256sum "$minimal_empty_permissions" "$minimal_sensors_manifest" \
    >>"$run_dir/input-hashes.log"
fi
if [[ $isolated_netd == true ]]; then
  sha256sum "$stock_netd" "$stock_netd_rc" \
    "$stock_netbpfload" "$stock_netbpfload_rc" \
    "$stock_netd_bpf_object" "$stock_clatd_bpf_object" \
    "$stock_dscp_policy_bpf_object" "$stock_service_connectivity_jar" \
    "$stock_platform_bpfloader" "$netbpfload_adapter_source" \
    "$connectivity_adapter_source" "$service_connectivity_adapter_source" \
    >>"$run_dir/input-hashes.log"
  find /sys/fs/bpf -xdev -mindepth 1 \
    -printf '%P type=%y mode=%m uid=%u gid=%g\n' 2>/dev/null | sort \
    >"$run_dir/host-bpffs-before.log"
fi
if [[ $permission_controller_projection == true ]]; then
  # Android's phone boot normally lets apexd activate the APEX set itself.
  # C1 instead supplies already-verified, host-mounted APEX files without
  # granting loop-control or device-mapper authority. Stock apexd currently
  # clears that reconstructed active set when its duplicate activation is
  # rejected. Project only the exact immutable permission-controller package
  # into the normal system scan path so PackageManager can cross that one
  # lifecycle mismatch. This does not synthesize a Binder service or APK.
  install -d -o root -g root -m 0755 "$permission_controller_target"
  permission_controller_target_created=true
  install -d -o root -g root -m 0755 "$extservices_target"
  extservices_target_created=true
  install -d -o root -g root -m 0755 "$sdk_sandbox_target"
  sdk_sandbox_target_created=true
  install -d -o root -g root -m 0755 "$connectivity_resources_target"
  connectivity_resources_target_created=true
  install -o root -g root -m 0644 /dev/null "$permission_controller_allowlist_target"
  permission_controller_allowlist_target_created=true
  install -o root -g root -m 0644 /dev/null "$extservices_allowlist_target"
  extservices_allowlist_target_created=true
  cat >>"$config" <<EOF
lxc.mount.entry = $permission_controller_source_dir system/priv-app/LumaGooglePermissionController none bind,ro,create=dir 0 0
lxc.mount.entry = $permission_controller_allowlist_source system/etc/permissions/luma-privapp-allowlist-com.google.android.permissioncontroller.xml none bind,ro,create=file 0 0
lxc.mount.entry = $extservices_source_dir system/priv-app/LumaGoogleExtServices none bind,ro,create=dir 0 0
lxc.mount.entry = $extservices_allowlist_source system/etc/permissions/luma-privapp-allowlist-com.google.android.ext.services.xml none bind,ro,create=file 0 0
lxc.mount.entry = $sdk_sandbox_source_dir system/app/LumaSdkSandboxGoogle none bind,ro,create=dir 0 0
lxc.mount.entry = $connectivity_resources_source_dir system/priv-app/LumaNetRes none bind,ro,create=dir 0 0
EOF
fi
if [[ $minimal_sensors_vintf == true ]]; then
  # C1 intentionally exposes no physical sensor device. Replace only the
  # stock one-HAL fragment in this mount namespace, so SensorService observes
  # that no sensor HAL is declared instead of waiting forever for hardware the
  # container is forbidden to access.
  cat >>"$config" <<EOF
lxc.mount.entry = $minimal_sensors_manifest vendor/etc/vintf/manifest/android.hardware.sensors-multihal.xml none bind,ro,create=file 0 0
EOF
fi
if [[ $minimal_wifi_vintf == true ]]; then
  # Cellular C1 owns no WiFi device and intentionally starts no WiFi stack.
  # Remove WiFi hardware feature declarations and its three device-manifest
  # fragments in this mount namespace. Keep the exact WiFi Direct feature:
  # QREL's framework resource enables WifiDisplayAdapter and explicitly
  # requires that declaration, even when the physical WiFi HAL is absent.
  for wifi_permission in \
    android.hardware.wifi.xml \
    android.hardware.wifi.aware.xml \
    android.hardware.wifi.passpoint.xml \
    android.hardware.wifi.rtt.xml; do
    printf 'lxc.mount.entry = %s vendor/etc/permissions/%s none bind,ro,create=file 0 0\n' \
      "$minimal_empty_permissions" "$wifi_permission" >>"$config"
  done
  for wifi_manifest in \
    android.hardware.wifi-service.xml \
    android.hardware.wifi.hostapd.xml \
    android.hardware.wifi.supplicant.xml; do
    printf 'lxc.mount.entry = %s vendor/etc/vintf/manifest/%s none bind,ro,create=file 0 0\n' \
      "$minimal_sensors_manifest" "$wifi_manifest" >>"$config"
  done
fi
if [[ $angle_null_renderer == true ]]; then
  sha256sum "$angle_egl_source" "$angle_gles1_source" "$angle_gles2_source" \
    "$angle_probe_source" \
    >>"$run_dir/input-hashes.log"
fi
record_luma_owners >"$run_dir/luma-owners-before.log"
printf 'mode=%s\nactive_clients_before=%s\n' \
  "$binder_mode" "$(binder_proc_count)" >"$run_dir/binder-boundary.log"
stdbuf -oL dmesg --follow-new --raw >"$run_dir/kernel-new.log" &
kernel_follow_pid=$!

if [[ $binder_mode == legacy-exclusive ]]; then
  # This kernel predates BinderFS but has the stock three Binder contexts
  # built in.  For this construction gate they are unused by the host, so
  # expose them exclusively to C1 and restore the immutable base config on
  # teardown.  Production remains gated on private BinderFS isolation.
  sed -i \
    -e '\#^lxc.hook.mount = .*/binderfs-hook.py$#d' \
    -e '\#^lxc.mount.entry = binder dev/binderfs binder #d' \
    "$config"
  ! grep -Eq 'binderfs-hook|binder dev/binderfs binder' "$config" ||
    die 'BinderFS directives remained in the legacy-boundary config'
  for binder_node in binder hwbinder vndbinder; do
    binder_original_modes[$binder_node]=$(stat -c '%a' "/dev/$binder_node")
    chmod 0666 "/dev/$binder_node"
    binder_major_hex=$(stat -c '%t' "/dev/$binder_node")
    binder_minor_hex=$(stat -c '%T' "/dev/$binder_node")
    binder_major=$((16#$binder_major_hex))
    binder_minor=$((16#$binder_minor_hex))
    cat >>"$config" <<EOF
lxc.cgroup2.devices.allow = c $binder_major:$binder_minor rwm
lxc.mount.entry = /dev/$binder_node dev/$binder_node none bind,create=file 0 0
EOF
  done
fi

if [[ $exact_apex_loop_metadata == true ]]; then
  [[ $(grep -c '/var/tmp/luma-stock-qrel1695-apex-runtime1/mounts/' "$config") -eq 123 ]] ||
    die 'base C1 APEX mount-entry count differs before runtime promotion'
  sed -i \
    's#/var/tmp/luma-stock-qrel1695-apex-runtime1/mounts/#/var/tmp/luma-stock-qrel1695-apex-runtime2/mounts/#g' \
    "$config"
  ! grep -q '/var/tmp/luma-stock-qrel1695-apex-runtime1/mounts/' "$config" ||
    die 'base C1 retained an old APEX runtime mount'
  apex_runtime2_mount_entries=123
  if [[ $permission_controller_projection == true ]]; then
    # Five existing PackageManager lifecycle projections plus the exact
    # connectivity-resources APK from the tethering APEX.
    apex_runtime2_mount_entries=$((apex_runtime2_mount_entries + 6))
  fi
  [[ $(grep -c '/var/tmp/luma-stock-qrel1695-apex-runtime2/mounts/' "$config") -eq \
     $apex_runtime2_mount_entries ]] ||
    die 'effective C1 APEX mount-entry count differs after runtime promotion'
fi

# Restore only this exact stock service definition after the base C1 policy
# disabled it. The lower QREL rc and binary stay immutable.
cat >"$override" <<'EOF'
# Temporary C1 KeyMint gate: exact QREL qseecomd, stock service semantics.
# Android's documented warn-once mode preserves the first ownership diagnostic
# and then prevents QREL's known legacy-fd mismatch from aborting the daemon.
# This container-local diagnostic property disappears with Android init.
on early-init
    setprop debug.fdsan warn_once
    # Android's supported headless-framework switch. C1 exposes no GPU, so
    # zygote must not preload an EGL implementation it cannot access.
    setprop ro.zygote.disable_gl_preload true
    # This kernel intentionally has no legacy ashmem driver. Android's own
    # libcutils memfd backend provides the equivalent sealed shared memory.
    setprop sys.use_memfd true

# Stock init conservatively resets this to false during post-fs-data for
# unknown vendor stacks. C1 is a controlled Android 16 userspace on a kernel
# with memfd sealing and no ashmem driver, so restore Android's own backend.
on property:sys.use_memfd=false
    setprop sys.use_memfd true

# The C1 rootfs bind-mounts an isolated, unencrypted container /data tree
# instead of the phone's userdata. On an SELinux-disabled host, stock vold's
# init_user0 stops at setfscreatecon before creating the normal user-0 roots.
# Reproduce only the Android 16 FsCrypt.cpp directory modes and /data/data bind
# topology, then emit the lifecycle event mount_all normally supplies. These
# paths remain below C1's private data bind and never touch Luma userdata.
on post-fs-data
    mkdir /data/data 0771 system system
    mkdir /data/user/0 0700 system system
    mount none /data/data /data/user/0 bind rec
    mkdir /data/system/users/0 0700 system system
    mkdir /data/misc/profiles/cur/0 0771 system system
    mkdir /data/system_de/0 0770 system system
    mkdir /data/vendor_de/0 0771 root root
    mkdir /data/misc_de/0 01771 system misc
    mkdir /data/user_de/0 0771 system system
    trigger nonencrypted

# The hardware-free second-stage container does not traverse Android's normal
# late-fs event, which is where class early_hal (and therefore keystore2) is
# started on the phone.  Preserve the stock service definition and start it
# only after exact qseecomd has published its real listener-ready property.
on property:vendor.sys.listeners.registered=true
    start keystore2

service vendor.qseecomd /vendor/bin/qseecomd
    override
    socket notify-topology stream 660 system drmrpc
    class core
    user root
    group root drmrpc
    setenv LD_LIBRARY_PATH /vendor/lib64/luma-qsee
EOF
if [[ $exact_audioserver == true ]]; then
  cat >>"$override" <<'EOF'

# Restore QREL's exact DevicesFactory service. AudioFlinger cannot publish
# until this stock HIDL factory exists. C1 exposes no /dev/snd or audio DSP
# endpoint, so the service can describe the stock API but cannot take physical
# audio ownership during this isolated construction gate.
on early-init
    # Select Android's exact stock no-device primary module through libhardware's
    # standard per-class selector. A later physical-audio gate deliberately
    # removes this property and uses QREL's volcano module with the real card.
    setprop ro.hardware.audio.primary default

service vendor.audio-hal /vendor/bin/hw/android.hardware.audio.service_64
    override
    class hal
    user audioserver
    group audio camera drmrpc inet media mediadrm net_bt net_bt_admin net_bw_acct oem_2901 wakelock oem_2912
    capabilities BLOCK_SUSPEND SYS_NICE
    ioprio rt 4
    writepid /dev/cpuset/foreground/tasks /dev/stune/foreground/tasks
    onrestart restart audioserver
EOF
fi
if [[ $angle_null_renderer == true ]]; then
  cat >>"$override" <<'EOF'

# Android's documented EGL-driver selector chooses an exact stock ANGLE
# payload copied under a container-only suffix. ANGLE's upstream null backend
# has no platform dependency and performs state validation without rendering.
on early-init
    setprop persist.graphics.egl luma_null
EOF
fi
if [[ $swangle_pastel_renderer == true ]]; then
  cat >>"$override" <<'EOF'

# Select QREL's own documented no-GPU branch. On a physical Android boot its
# helper derives this property from the SoC subset mask; C1 intentionally does
# not expose that physical sysfs topology, so replace only that detector and
# set the same property it would set on a no-GPU target. QREL's unchanged
# early-fs action then selects its stock ANGLE frontend and CPU-only Vulkan
# Pastel (SwiftShader) backend.
service vendor_qti_graphics_boot /system/bin/true
    override
    disabled
    oneshot
    user root
    group root

on early-init
    setprop vendor.display.gpu_rendering false
    setprop ro.hardware.vulkan pastel
    setprop persist.graphics.egl luma_swangle
EOF
fi
if [[ $aosp_noop_composer == true ]]; then
  cat >>"$override" <<'EOF'

# AOSP's official Ranchu composer has a no-op mode and a synthetic display
# finder specifically for environments without virtio-gpu or DRM. These are
# read-only properties set before either service starts.
on early-init
    setprop ro.vendor.hwcomposer.mode noop
    setprop ro.vendor.hwcomposer.display_finder_mode noop

service vendor.hwcomposer-3 /vendor/bin/hw/android.hardware.graphics.composer3-service.ranchu
    class hal animation
    user system
    group graphics drmrpc
    capabilities SYS_NICE
    setenv LD_LIBRARY_PATH /vendor/lib64/luma-noop-composer
    onrestart restart surfaceflinger
    task_profiles ServiceCapacityLow
EOF
fi
if [[ $exact_stock_allocator == true ]]; then
  cat >>"$override" <<'EOF'

# Restore QREL's exact stock graphics allocator service definition, which the
# hardware-free C1 base blocks alongside the physical display stack. This gate
# exposes only the RAM-only AOSP system DMA-BUF heap, never a Qualcomm secure
# heap, SMMU proxy, DRM, KGSL, framebuffer, input, modem, or audio endpoint.
service vendor.qti.hardware.display.allocator /vendor/bin/hw/vendor.qti.hardware.display.allocator-service
    override
    class hal animation
    user system
    group graphics drmrpc
    capabilities SYS_NICE
    onrestart restart surfaceflinger

on early-init
    start vendor.qti.hardware.display.allocator
EOF
fi
if [[ $empty_sensors_hal == true ]]; then
  cat >>"$override" <<'EOF'

# The stock SensorService waits for the VINTF-declared AIDL sensor HAL. C1
# exposes no physical sensor devices or Qualcomm sensor subprocess, so run the
# exact QREL AOSP Multi-HAL against an empty, read-only sub-HAL list. It
# publishes the authentic zero-sensor API and cannot synthesize measurements.
service vendor.sensors-hal-multihal /vendor/bin/hw/android.hardware.sensors-service.multihal
    override
    class hal
    user system
    group system wakelock context_hub input uhid
    task_profiles ServiceCapacityLow
    capabilities BLOCK_SUSPEND
    rlimit rtprio 10 10

on early-init
    start vendor.sensors-hal-multihal
EOF
fi
if [[ $exact_apex_loop_metadata == true ]]; then
  # Keep the backing pathname identical to the path reported by sysfs, as
  # required by apexd's normal loop-device mount discovery.
  cat >>"$config" <<EOF
lxc.mount.entry = $apex_runtime/images var/tmp/luma-stock-qrel1695-apex-runtime1/images none bind,ro,create=dir 0 0
EOF
  for apex_index in "${!apex_loop_nodes[@]}"; do
    cat >>"$config" <<EOF
lxc.cgroup2.devices.allow = b ${apex_loop_majors[$apex_index]}:${apex_loop_minors[$apex_index]} r
lxc.mount.entry = ${apex_loop_nodes[$apex_index]} dev/block/${apex_loop_nodes[$apex_index]#/dev/} none bind,ro,create=file 0 0
EOF
  done
fi
if [[ $empty_sensors_hal == true ]]; then
  install -o root -g root -m 0444 /dev/null "$empty_sensors_config"
  empty_sensors_config_created=true
  [[ $(sha256sum "$empty_sensors_config" | cut -d' ' -f1) == \
     e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 ]] ||
    die 'container-only empty sensors configuration hash mismatch'
  sha256sum "$empty_sensors_config" >>"$run_dir/input-hashes.log"
  cat >>"$config" <<EOF
lxc.mount.entry = $empty_sensors_config vendor/etc/sensors/hals.conf none bind,ro,create=file 0 0
EOF
fi
if [[ $exact_audioserver == true ]]; then
  cat >>"$config" <<EOF
# Hardware-free framework projection: omit only SoundTrigger, whose physical
# DSP is intentionally absent. The stock manifest remains unchanged on disk.
lxc.mount.entry = $framework_audio_manifest vendor/etc/vintf/manifest/manifest_non_qmaa.xml none bind,ro,create=file 0 0
EOF
fi
if [[ $isolated_netd == true ]]; then
  # The adapter is a disposable system-overlay executable. It invokes the
  # exact stock libbpf_android ABI on the exact stock signed netd.o; only the
  # Android 16 kernel-LTS/sysctl preflight is replaced for the private C1
  # namespace. The immutable lower system image is never changed.
  install -o root -g root -m 0755 \
    "$netbpfload_adapter_source" "$netbpfload_adapter_target"
  netbpfload_adapter_created=true
  [[ $(sha256sum "$netbpfload_adapter_target" | cut -d' ' -f1) == \
     "$expected_netbpfload_adapter_sha" ]] ||
    die 'installed netbpfload adapter hash mismatch'
  # A fresh BPF filesystem is created in C1's mount namespace for this boot.
  # It neither bind-mounts Fedora's host pin tree nor survives container
  # teardown. Network programs can only attach inside C1's empty netns.
  cat >>"$config" <<EOF
lxc.mount.entry = bpf sys/fs/bpf bpf rw,nodev,noexec,nosuid,create=dir 0 0
lxc.mount.entry = $connectivity_adapter_source apex/com.android.tethering@360671220/lib64/libservice-connectivity.so none bind,ro,create=file 0 0
lxc.mount.entry = $connectivity_adapter_source apex/com.android.tethering/lib64/libservice-connectivity.so none bind,ro,create=file 0 0
lxc.mount.entry = $service_connectivity_adapter_source apex/com.android.tethering@360671220/javalib/service-connectivity.jar none bind,ro,create=file 0 0
lxc.mount.entry = $service_connectivity_adapter_source apex/com.android.tethering/javalib/service-connectivity.jar none bind,ro,create=file 0 0
EOF
  # Android init must be able to place CAP_BPF in exact stock netd's effective
  # set. Keep that one capability at the LXC boundary for this disposable run;
  # cleanup restores the hash-verified base configuration. C1 still has an
  # empty network namespace and private bpffs, so netd cannot attach programs
  # to Fedora's interfaces or alter Fedora's persistent BPF pins.
  [[ $(grep -c '^lxc\.cap\.keep = ' "$config") -eq 1 ]] ||
    die 'unexpected LXC capability boundary topology'
  ! grep -Eq '^lxc\.cap\.keep = .*([[:space:]]|^)bpf([[:space:]]|$)' "$config" ||
    die 'base C1 capability boundary already contains bpf'
  sed -i '/^lxc\.cap\.keep = / s/$/ bpf/' "$config"
fi
chmod 0600 "$override"
override_created=true

if [[ $isolated_netd == true ]]; then
  # QREL preloads the legacy iptables table modules before netd starts. Fedora
  # builds the same support as modules, so make only those tables available for
  # C1's private empty network namespace. Record exactly what this gate added;
  # cleanup unloads it after the namespace is destroyed.
  for module_name in "${netfilter_gate_modules[@]}"; do
    module_path=$(modinfo -n "$module_name" 2>/dev/null || true)
    [[ -n $module_path && -f $module_path && ! -L $module_path ]] ||
      die "required netfilter module is absent or linked: $module_name"
    [[ $(modinfo -F vermagic "$module_name" | awk '{print $1}') == "$expected_kernel" ]] ||
      die "netfilter module vermagic mismatch: $module_name"
    sha256sum "$module_path" >>"$run_dir/input-hashes.log"
    if ! grep -qw "$module_name" /proc/modules; then
      netfilter_new_modules+=("$module_name")
      modprobe "$module_name"
    fi
  done
  for module_name in "${netfilter_custom_modules[@]}"; do
    ! grep -qw "$module_name" /proc/modules ||
      die "custom netfilter module was already loaded: $module_name"
    netfilter_new_modules+=("$module_name")
    insmod "$netfilter_custom_module_dir/$module_name.ko"
  done
fi

if [[ $exact_apexd == true ]]; then
  # Android init refuses a vendor rc file that replaces a system service across
  # the Treble boundary. Put the temporary replacement in the same /system init
  # directory as both the stock definition and C1's later lifecycle adapter so
  # this lexically-last service definition is the one init actually accepts.
  cat >"$apex_override" <<'EOF'
# Hardware-free framework extension: publish Android's authentic APEX Binder
# service from exact stock apexd. The container device allowlist exposes no
# loop-control, device-mapper control, or block device, and the verified APEX
# payload set is already mounted read-only by the host.
service apexd /system/bin/apexd
    override
    interface aidl apexservice
    class core
    user root
    group system
    oneshot
    disabled
    capabilities CHOWN DAC_OVERRIDE DAC_READ_SEARCH FOWNER SYS_ADMIN

EOF
  if [[ $isolated_netd == true ]]; then
    cat >>"$apex_override" <<'EOF'

# Android 16's exact netbpfload refuses Linux 7.1 at its LTS-policy preflight
# and then requires a writable host-global BPF sysctl. The narrow Luma adapter
# invokes the exact stock libbpf_android ABI on the exact signed QREL netd.o
# inside C1's private bpffs. It publishes bpf.progs_loaded only after a real
# load and required-pin check; no kernel-version/property spoof is involved.
service bpfloader /system/bin/luma-netbpfload
    override
    setenv LUMA_C1_NETD_ONLY 1
    capabilities CHOWN SYS_ADMIN NET_ADMIN
    group root graphics network_stack net_admin net_bw_acct net_bw_stats net_raw system
    user root
    rlimit memlock 1073741824 1073741824
    oneshot

# Stock netd is marked `updatable`, so init normally defers it until
# apexd.status=ready. C1 truthfully reaches `activated` after reconstructing
# the hash-verified, host-mounted APEX inventory, but it cannot finish the
# unrelated device-encrypted APEX snapshot phase. This separately gated
# definition removes only that lifecycle deferral; the executable, sockets,
# UID/GID, capabilities, and restart behavior remain stock. LXC gives it a
# private empty network namespace, so it cannot see Fedora interfaces or take
# modem ownership.
service netd /system/bin/netd
    override
    disabled
    class main
    capabilities BPF CHOWN DAC_OVERRIDE DAC_READ_SEARCH FOWNER IPC_LOCK KILL NET_ADMIN NET_BIND_SERVICE NET_RAW SETUID SETGID
    user root
    group root net_admin
    rlimit memlock 1073741824 1073741824
    socket dnsproxyd stream 0660 root inet
    socket mdns stream 0660 root system
    socket fwmarkd stream 0660 root inet
    onrestart restart zygote
    onrestart restart zygote_secondary

on property:apexd.status=activated && property:keystore.module_hash.sent=true
    start bpfloader

on property:bpf.progs_loaded=1
    start netd
EOF
  else
    cat >>"$apex_override" <<'EOF'

# The hardware-free construction gate has no Android network runtime. Keep it
# from invoking netbpfload's physical-phone fatal reboot policy.
service bpfloader /system/bin/false
    override
    disabled
    oneshot
    user root
    group root
EOF
  fi
  cat >>"$apex_override" <<'EOF'

# update_verifier waits indefinitely for the physical boot-control HAL, which
# C1 intentionally does not expose. It is an OTA slot-health verifier, not a
# Java-framework or telephony dependency, so complete this construction-only
# exec_start without granting the container boot-partition authority.
service update_verifier /system/bin/true
    override
    disabled
    oneshot
    user root
    group root

# Fedora has SELinux disabled on this physical prototype. The preloaded hook
# succeeds only while /sys/fs/selinux/enforce is absent and otherwise fails
# closed; it lets stock zygote fork system_server without inventing labels.
service zygote /system/bin/app_process64 -Xzygote /system/bin --zygote --start-system-server --socket-name=zygote
    override
    class main
    priority -20
    user root
    group root readproc reserved_disk
    socket zygote stream 660 root system
    socket usap_pool_primary stream 660 root system
    setenv LD_PRELOAD /system/lib64/libluma_selinux_disabled.so
    onrestart exec_background - system system -- /system/bin/vdc volume abort_fuse
    onrestart write /sys/power/state on
    onrestart write /sys/power/wake_lock zygote_kwl
    onrestart restart audioserver
    onrestart restart cameraserver
    onrestart restart media
    onrestart restart --only-if-running media.tuner
    onrestart restart netd
    onrestart restart wificond
    task_profiles ProcessCapacityHigh MaxPerformance
    critical window=${zygote.critical_window.minute:-off} target=zygote-fatal

# Stock installd remains the sole app-data owner. On this SELinux-disabled
# prototype only its restorecon calls are process-locally treated as no-ops;
# directory creation, ownership, modes, quotas, and package isolation remain
# the exact QREL implementation. The hook fails closed if SELinux is active.
service installd /system/bin/installd
    override
    class main
    user root
    capabilities CHOWN DAC_OVERRIDE DAC_READ_SEARCH FOWNER FSETID KILL SETGID SETUID SYS_ADMIN
    setenv LD_PRELOAD /system/lib64/libluma_restorecon_disabled.so
EOF
  if [[ $exact_audioserver == true ]]; then
    cat >>"$apex_override" <<'EOF'

# Restore QREL's exact stock audioserver so AudioService can publish its real
# media.audio_flinger and media.audio_policy Binder APIs. C1 still receives no
# /dev/snd nodes and every physical vendor audio HAL remains disabled; this is
# an isolated framework-construction gate, not a physical audio cutover.
service audioserver /system/bin/audioserver
    override
    class core
    user audioserver
    group audio camera drmrpc media mediadrm net_bt net_bt_admin net_bw_acct wakelock
    capabilities BLOCK_SUSPEND
    rlimit rtprio 10 10
    ioprio rt 4
    task_profiles ProcessCapacityHigh HighPerformance
    onrestart restart vendor.audio-hal
    onrestart restart vendor.audio-hal-aidl
    onrestart restart vendor.audio-effect-hal-aidl
    onrestart restart vendor.audio-hal-4-0-msd
    onrestart restart audio_proxy_service

# Android's exact GPU service cannot publish without KGSL, which is purposely
# absent from this CPU-rendered construction container. Suppress that unusable
# process instead of allowing a repeating SIGABRT loop to masquerade as a
# healthy framework. SurfaceFlinger remains the separately gated stock binary.
service gpu /system/bin/false
    override
    disabled
    oneshot
    user root
    group root
EOF
  fi
  if [[ $exact_surfaceflinger == true ]]; then
    cat >>"$apex_override" <<'EOF'

# Restore only the hash-verified stock SurfaceFlinger definition that the
# hardware-free base policy disabled. Qualcomm's physical composer service
# remains blocked and C1 receives no DRM, KGSL, framebuffer, input, modem, or
# audio device. This tests whether stock SurfaceFlinger can supply its real
# Binder API at Android's supported no-HWC boundary; it does not emulate or
# proxy a display service.
service surfaceflinger /system/bin/surfaceflinger
    override
    class core animation
    user system
    group graphics drmrpc readproc
    capabilities SYS_NICE
    onrestart restart --only-if-running zygote
    task_profiles HighPerformance
EOF
  fi
  chmod 0600 "$apex_override"
  apex_override_created=true
fi

# Stock qseecomd has one indivisible listener set and refuses to publish even
# its time listener unless an 8 KiB `ssd` backing object opens read/write. C1
# must not expose the phone's real partition, so give this construction gate a
# disposable zero-seeded regular file at the stock pathname. Any opaque writes
# are destroyed during cleanup and are not logged. This proves process/listener
# construction only; it is explicitly not production secure-storage proof.
install -d -o root -g root -m 0711 "$ssd_scratch"
dd if=/dev/zero of="$ssd_scratch/ssd" bs=8192 count=1 status=none
chmod 0600 "$ssd_scratch/ssd"
# PersistentDataBlockService is a required phase-500 framework service, but C1
# must never see the phone's real FRP partition. Give the isolated instance a
# disposable regular file large enough for Android's PDB header and payload.
# Only Android's system UID may read or format it; cleanup destroys it without
# recording its post-initialization contents.
dd if=/dev/zero of="$ssd_scratch/frp" bs=512 count=1024 status=none
chmod 0600 "$ssd_scratch/frp"
[[ $(sha256sum "$ssd_scratch/frp" | cut -d' ' -f1) == \
   07854d2fef297a06ba81685e660c332de36d5d18d546927d30daad6d7fda1541 ]] ||
  die 'ephemeral FRP file did not begin as exact zero-seeded 512 KiB storage'
ssd_scratch_created=true
frp_loop_node=$(losetup --find --show "$ssd_scratch/frp")
[[ $frp_loop_node =~ ^/dev/loop[0-9]+$ && -b $frp_loop_node ]] ||
  die 'ephemeral FRP loop device was not created'
frp_loop_mode=$(stat -c '%a' "$frp_loop_node")
frp_loop_uid=$(stat -c '%u' "$frp_loop_node")
frp_loop_gid=$(stat -c '%g' "$frp_loop_node")
chown 1000:1000 "$frp_loop_node"
chmod 0600 "$frp_loop_node"
frp_loop_major=$((16#$(stat -c '%t' "$frp_loop_node")))
frp_loop_minor=$((16#$(stat -c '%T' "$frp_loop_node")))
frp_loop_name=${frp_loop_node#/dev/}
[[ $(cat "/sys/class/block/$frp_loop_name/loop/backing_file") == \
   "$ssd_scratch/frp" ]] || die 'ephemeral FRP loop backing file differs'
# QREL qseecomd resolves libdmabufheap from the vendor namespace, but this
# factory vendor image has no file at that pathname. LXC cannot create a bind
# target after the vendor overlay is mounted read-only, so place the already
# hash-verified adapter in the writable, disposable overlay before startup.
# Cleanup removes it again; the immutable lower vendor image is never changed.
install -d -o root -g root -m 0755 "$vendor_dmabuf_dir"
vendor_dmabuf_dir_created=true
install -o root -g root -m 0644 \
  "$qseecomd_dmabuf_adapter" "$vendor_dmabuf_target"
vendor_dmabuf_created=true
[[ $(sha256sum "$vendor_dmabuf_target" | cut -d' ' -f1) == "$expected_dmabuf_adapter_sha" ]] ||
  die 'installed vendor DMA-buffer adapter hash mismatch'

# KeyMint's transitive dependency is resolved in Android's system default
# namespace on this extracted QREL image, while qseecomd resolves the same
# SONAME in the vendor namespace. The LXC root is read-only before late mount
# entries are applied, so create this disposable overlay file before startup.
# It is the same hash-locked adapter and is removed during cleanup.
install -o root -g root -m 0644 \
  "$qseecomd_mink_adapter" "$system_mink_target"
system_mink_created=true
[[ $(sha256sum "$system_mink_target" | cut -d' ' -f1) == "$expected_mink_adapter_sha" ]] ||
  die 'installed system Mink adapter hash mismatch'

if [[ $framework_required == true ]]; then
  install -o root -g root -m 0644 \
    "$selinux_disabled_shim" "$selinux_disabled_target"
  selinux_disabled_created=true
  [[ $(sha256sum "$selinux_disabled_target" | cut -d' ' -f1) == "$expected_selinux_disabled_shim_sha" ]] ||
    die 'installed SELinux-disabled compatibility shim hash mismatch'
  install -o root -g root -m 0644 \
    "$restorecon_disabled_shim" "$restorecon_disabled_target"
  restorecon_disabled_created=true
  [[ $(sha256sum "$restorecon_disabled_target" | cut -d' ' -f1) == \
     "$expected_restorecon_disabled_shim_sha" ]] ||
    die 'installed restorecon-disabled compatibility hook hash mismatch'
fi

if [[ $angle_null_renderer == true ]]; then
  install -o root -g root -m 0644 "$angle_egl_source" "$angle_egl_target"
  install -o root -g root -m 0644 "$angle_gles1_source" "$angle_gles1_target"
  install -o root -g root -m 0644 "$angle_gles2_source" "$angle_gles2_target"
  install -o root -g root -m 0755 "$angle_probe_source" "$angle_probe_target"
  angle_null_created=true
  angle_probe_created=true
  [[ $(sha256sum "$angle_egl_target" | cut -d' ' -f1) == "$expected_angle_egl_sha" &&
     $(sha256sum "$angle_gles1_target" | cut -d' ' -f1) == "$expected_angle_gles1_sha" &&
     $(sha256sum "$angle_gles2_target" | cut -d' ' -f1) == "$expected_angle_gles2_sha" ]] ||
    die 'installed ANGLE null payload hash mismatch'
  [[ $(sha256sum "$angle_probe_target" | cut -d' ' -f1) == "$expected_angle_probe_sha" ]] ||
    die 'installed ANGLE null probe hash mismatch'
fi
if [[ $swangle_pastel_renderer == true ]]; then
  swangle_created=true
  swangle_probe_created=true
  install -o root -g root -m 0644 "$angle_egl_source" "$swangle_egl_target"
  install -o root -g root -m 0644 "$angle_gles1_source" "$swangle_gles1_target"
  install -o root -g root -m 0644 "$angle_gles2_source" "$swangle_gles2_target"
  install -o root -g root -m 0755 "$swangle_probe_source" "$swangle_probe_target"
  [[ $(sha256sum "$swangle_egl_target" | cut -d' ' -f1) == "$expected_angle_egl_sha" &&
     $(sha256sum "$swangle_gles1_target" | cut -d' ' -f1) == "$expected_angle_gles1_sha" &&
     $(sha256sum "$swangle_gles2_target" | cut -d' ' -f1) == "$expected_angle_gles2_sha" ]] ||
    die 'installed SwANGLE payload hash mismatch'
  [[ $(sha256sum "$swangle_probe_target" | cut -d' ' -f1) == "$expected_swangle_probe_sha" ]] ||
    die 'installed SwANGLE/Pastel probe hash mismatch'
fi
if [[ $aosp_noop_composer == true ]]; then
  noop_composer_created=true
  install -d -o root -g root -m 0755 "$noop_composer_lib_dir_target"
  install -o root -g root -m 0755 \
    "$noop_composer_source/bin/hw/android.hardware.graphics.composer3-service.ranchu" \
    "$noop_composer_target"
  for noop_lib in "${noop_composer_lib_names[@]}"; do
    install -o root -g root -m 0644 "$noop_composer_source/lib64/$noop_lib" \
      "$noop_composer_lib_dir_target/$noop_lib"
  done
  [[ $(sha256sum "$noop_composer_target" | cut -d' ' -f1) == \
     "$expected_noop_composer_sha" ]] ||
    die 'installed AOSP no-op composer hash mismatch'
fi

[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 0 ]] ||
  die 'preexisting TEE nodes found'
insmod "$module"
module_loaded=true
[[ -c /dev/tee0 ]] || die 'QCOMTEE client node was not created'
[[ $(find /dev -maxdepth 1 -type c \( -name 'tee[0-9]*' -o -name 'teepriv[0-9]*' \) | wc -l) -eq 1 ]] ||
  die 'unexpected QCOMTEE node topology'
tee_mode=$(stat -c %a /dev/tee0)
chmod 0666 /dev/tee0

tee_major=$(stat -c '%t' /dev/tee0)
tee_minor=$(stat -c '%T' /dev/tee0)
tee_major=$((16#$tee_major))
tee_minor=$((16#$tee_minor))
if [[ $exact_stock_allocator == true ]]; then
  system_heap_mode=$(stat -c '%a' /dev/dma_heap/system)
  chmod 0666 /dev/dma_heap/system
  heap_major=$(stat -c '%t' /dev/dma_heap/system)
  heap_minor=$(stat -c '%T' /dev/dma_heap/system)
  heap_major=$((16#$heap_major))
  heap_minor=$((16#$heap_minor))
fi
cat >>"$config" <<EOF
lxc.cgroup2.devices.allow = c $tee_major:$tee_minor rwm
lxc.mount.entry = /dev/tee0 dev/tee0 none bind,create=file 0 0
# Exact QREL qseecomd performs a stock pathname availability check before its
# linked Mink transport starts. Both paths below are the same unprivileged
# QCOMTEE client device; this is an in-container compatibility name, not a
# second secure-world endpoint.
lxc.mount.entry = /dev/tee0 dev/smcinvoke none bind,create=file 0 0
lxc.mount.entry = $ssd_scratch dev/block/bootdevice/by-name none bind,create=dir 0 0
lxc.mount.entry = $qseecomd_mink_adapter vendor/lib64/libminkdescriptor.so none bind,ro,create=file 0 0
EOF
if [[ $exact_audioserver == true ]]; then
  cat >>"$config" <<EOF
# Ephemeral regular-file-backed loop device for Android's mandatory
# PersistentDataBlockService. This is not the phone's FRP partition.
lxc.cgroup2.devices.allow = b $frp_loop_major:$frp_loop_minor rwm
lxc.mount.entry = $frp_loop_node dev/block/bootdevice/by-name/frp none bind,create=file 0 0
EOF
fi
if [[ $exact_stock_allocator == true ]]; then
  cat >>"$config" <<EOF
lxc.cgroup2.devices.allow = c $heap_major:$heap_minor rwm
lxc.mount.entry = /dev/dma_heap/system dev/dma_heap/system none bind,create=file 0 0
# QREL's libdmabufheap names the ordinary nonsecure system heap
# qcom,system. This is a second pathname to the exact same RAM-only AOSP
# system heap node, not an additional Qualcomm or secure allocation endpoint.
lxc.mount.entry = /dev/dma_heap/system dev/dma_heap/qcom,system none bind,create=file 0 0
EOF
fi

(
  umask 000
  exec lxc-start -P "$lxc_path" -n "$name" -d -l TRACE -o "$run_dir/lxc.log"
)
container_started=true

# Begin the complete Android log before evaluating any secure-service result.
for _ in {1..100}; do
  if lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
       /system/bin/logcat -g >/dev/null 2>&1; then
    # This logd instance is created with the ephemeral container. Preserve its
    # earliest records: apexd reconstructs its mount database before this
    # host-side collector can attach, so clearing here would erase the causal
    # diagnostics for that exact lifecycle boundary.
    lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/logcat -b all -v threadtime >"$run_dir/logcat.log" 2>&1 &
    logcat_pid=$!
    break
  fi
  sleep 0.1
done
[[ -n $logcat_pid ]] || die 'Android logcat could not be started'

if [[ $framework_required == true ]]; then
  user0_ready=false
  for _ in {1..300}; do
    installd_state=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop init.svc.installd 2>/dev/null || true)
    data_data_stat=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/stat -c '%a:%u:%g:%d:%i' /data/data 2>/dev/null || true)
    data_user0_stat=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/stat -c '%a:%u:%g:%d:%i' /data/user/0 2>/dev/null || true)
    if [[ $installd_state == running && $data_data_stat == 771:1000:1000:* &&
          $data_user0_stat == 771:1000:1000:* &&
          ${data_data_stat#*:*:*:} == "${data_user0_stat#*:*:*:}" ]]; then
      user0_ready=true
      break
    fi
    sleep 0.1
  done
  {
    printf 'init.svc.installd=%s\n' "$installd_state"
    printf 'data_data=%s\n' "$data_data_stat"
    printf 'data_user_0=%s\n' "$data_user0_stat"
    for user0_path in \
      /data/system/users/0 /data/misc/profiles/cur/0 \
      /data/system_de/0 /data/vendor_de/0 /data/misc_de/0 /data/user_de/0; do
      printf '%s=' "$user0_path"
      lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/stat -c '%a:%u:%g' "$user0_path" 2>/dev/null || true
    done
  } >"$run_dir/container-user0-state.log"
  [[ $user0_ready == true ]] ||
    die 'isolated Android user-0 data topology did not initialize'
  grep -qx '/data/system/users/0=700:1000:1000' "$run_dir/container-user0-state.log" ||
    die 'Android user-0 system legacy directory differs'
  grep -qx '/data/misc/profiles/cur/0=771:1000:1000' "$run_dir/container-user0-state.log" ||
    die 'Android user-0 profile directory differs'
  grep -qx '/data/system_de/0=770:1000:1000' "$run_dir/container-user0-state.log" ||
    die 'Android user-0 system DE directory differs'
  grep -qx '/data/vendor_de/0=771:0:0' "$run_dir/container-user0-state.log" ||
    die 'Android user-0 vendor DE directory differs'
  grep -qx '/data/misc_de/0=1771:1000:9998' "$run_dir/container-user0-state.log" ||
    die 'Android user-0 misc DE directory differs'
  grep -qx '/data/user_de/0=771:1000:1000' "$run_dir/container-user0-state.log" ||
    die 'Android user-0 app DE directory differs'
fi

if [[ $exact_stock_allocator == true ]]; then
  allocator_ready=false
  for _ in {1..200}; do
    [[ $(lxc-info -P "$lxc_path" -n "$name" -sH 2>/dev/null || true) == RUNNING ]] ||
      die 'container stopped before stock graphics allocator publication'
    allocator_state=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop init.svc.vendor.qti.hardware.display.allocator \
      2>/dev/null || true)
    allocator_pid=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/pidof vendor.qti.hardware.display.allocator-service \
      2>/dev/null || true)
    allocator_services=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/service list 2>/dev/null || true)
    if [[ $allocator_state == running && $allocator_pid =~ ^[0-9]+$ ]] &&
       grep -Fq 'android.hardware.graphics.allocator.IAllocator/default' \
         <<<"$allocator_services"; then
      allocator_ready=true
      break
    fi
    sleep 0.1
  done
  {
    printf 'init.svc.vendor.qti.hardware.display.allocator=%s\n' "$allocator_state"
    printf 'pid=%s\n' "$allocator_pid"
    printf 'binder_service=%s\n' "$allocator_ready"
    printf 'heap_device=%s\n' \
      "$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/stat -c '%t:%T' /dev/dma_heap/system 2>/dev/null || true)"
    printf 'qcom_system_heap_alias=%s\n' \
      "$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/stat -c '%t:%T' '/dev/dma_heap/qcom,system' 2>/dev/null || true)"
  } >"$run_dir/stock-allocator-state.log"
  [[ $allocator_ready == true ]] || die 'stock graphics allocator did not publish'
fi

if [[ $angle_null_renderer == true ]]; then
  lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /vendor/bin/luma-angle-null-probe >"$run_dir/angle-null-probe.log" 2>&1 ||
    die 'direct stock ANGLE null-renderer probe failed'
  grep -qx 'ANGLE_NULL_DIRECT_GATE=true' "$run_dir/angle-null-probe.log" ||
    die 'direct stock ANGLE null-renderer probe did not publish acceptance'
fi
if [[ $swangle_pastel_renderer == true ]]; then
  {
    printf 'ro.hardware.vulkan='
    lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop ro.hardware.vulkan
    printf 'persist.graphics.egl='
    lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop persist.graphics.egl
    printf 'vendor.display.gpu_rendering='
    lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop vendor.display.gpu_rendering
    printf 'ro.hardware.egl='
    lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop ro.hardware.egl
  } >"$run_dir/swangle-pastel-properties.log"
  grep -qx 'ro.hardware.vulkan=pastel' "$run_dir/swangle-pastel-properties.log" ||
    die 'container did not select the CPU-only Vulkan Pastel driver'
  grep -Eq '^persist.graphics.egl=(luma_swangle)?$' \
    "$run_dir/swangle-pastel-properties.log" ||
    die 'container selected an unexpected persistent EGL override'
  grep -qx 'vendor.display.gpu_rendering=false' "$run_dir/swangle-pastel-properties.log" ||
    die 'container did not retain QREL no-GPU mode'
  grep -qx 'ro.hardware.egl=angle' "$run_dir/swangle-pastel-properties.log" ||
    die 'QREL no-GPU mode did not select stock ANGLE'
  lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /vendor/bin/luma-swangle-pastel-probe \
    >"$run_dir/swangle-pastel-probe.log" 2>&1 ||
    die 'direct stock SwANGLE/Pastel probe failed'
  grep -qx 'SWANGLE_PASTEL_DIRECT_GATE=true' "$run_dir/swangle-pastel-probe.log" ||
    die 'direct stock SwANGLE/Pastel probe did not publish acceptance'
  grep -Eq '^gl_renderer=.*SwiftShader' "$run_dir/swangle-pastel-probe.log" ||
    die 'direct stock SwANGLE/Pastel probe did not identify SwiftShader'
fi

# Readiness must be published by exact qseecomd. Never synthesize it here.
qsee_seen=false
for _ in {1..200}; do
  [[ $(lxc-info -P "$lxc_path" -n "$name" -sH 2>/dev/null || true) == RUNNING ]] ||
    die 'container stopped before qseecomd readiness'
  qsee_pid=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /system/bin/pidof qseecomd 2>/dev/null || true)
  qsee_state=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /system/bin/getprop init.svc.vendor.qseecomd 2>/dev/null || true)
  [[ $qsee_state == running ]] && qsee_seen=true
  if [[ $qsee_seen == true && $qsee_state != running ]]; then
    die "stock qseecomd left running state before readiness: $qsee_state"
  fi
  listeners=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /system/bin/getprop vendor.sys.listeners.registered 2>/dev/null || true)
  if [[ $qsee_pid =~ ^[0-9]+$ && $listeners == true ]]; then
    printf 'publisher=/vendor/bin/qseecomd\npid=%s\nproperty=vendor.sys.listeners.registered\nvalue=true\n' \
      "$qsee_pid" >"$run_dir/listener-readiness.log"
    break
  fi
  sleep 0.1
done
[[ -s $run_dir/listener-readiness.log ]] || die 'exact qseecomd did not publish listener readiness'

for _ in {1..300}; do
  [[ $(lxc-info -P "$lxc_path" -n "$name" -sH 2>/dev/null || true) == RUNNING ]] ||
    die 'container stopped before KeyMint publication'
  if grep -Eiq 'get_date_support failed|KeymasterUtils initialization failed|Abort message.*keymint|keymint.*fatal' \
       "$run_dir/logcat.log"; then
    die 'KeyMint aborted before publication'
  fi
  services=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /system/bin/service list 2>/dev/null || true)
  printf '%s\n' "$services" >"$run_dir/services.last.log"
  if grep -Fq 'android.hardware.security.keymint.IKeyMintDevice/default' <<<"$services" &&
     grep -Fq 'android.hardware.security.sharedsecret.ISharedSecret/default' <<<"$services" &&
     grep -Fq 'android.hardware.security.secureclock.ISecureClock/default' <<<"$services" &&
     grep -Fq 'android.hardware.security.keymint.IRemotelyProvisionedComponent/default' <<<"$services" &&
     grep -Fq 'android.security.maintenance' <<<"$services"; then
    printf '%s\n' "$services" >"$run_dir/services.log"
    break
  fi
  sleep 0.1
done
if [[ ! -s $run_dir/services.log ]]; then
  {
    printf 'keystore2_state='
    lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop init.svc.keystore2 2>/dev/null || true
    printf 'keymint_state='
    lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop init.svc.vendor.keymint-qti 2>/dev/null || true
    printf 'keystore2_pid='
    lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/pidof keystore2 2>/dev/null || true
    printf 'keymint_pid='
    lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/pidof android.hardware.security.keymint-service-qti \
      2>/dev/null || true
  } >"$run_dir/security-service-state.log"
  die 'KeyMint/Keystore publication timed out'
fi

if [[ $exact_apex_loop_metadata == true ]]; then
  apex_service_ready=false
  for _ in {1..600}; do
    apex_status=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop apexd.status 2>/dev/null || true)
    lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/cmd apexservice getAllPackages \
      >"$run_dir/apex-all-packages.log" 2>&1 || true
    lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/cmd apexservice getActivePackages \
      >"$run_dir/apex-active-packages.log" 2>&1 || true
    if [[ $apex_status == activated || $apex_status == ready ]] &&
       grep -Fq 'com.android.permission' "$run_dir/apex-all-packages.log"; then
      apex_service_ready=true
      break
    fi
    sleep 0.1
  done
  [[ $apex_service_ready == true ]] ||
    die 'stock apexd did not publish its verified package inventory'
  apex_active_count=$(grep -c '^Module:' \
    "$run_dir/apex-active-packages.log" || true)
  if [[ $permission_controller_projection == true ]]; then
    [[ $apex_active_count -eq 0 ]] ||
      die "permission projection is unsafe with an active APEX set: $apex_active_count"
    projected_permission_controller=/proc/$(lxc-info -P "$lxc_path" -n "$name" -pH)/root/system/priv-app/LumaGooglePermissionController/GooglePermissionController.apk
    [[ -f $projected_permission_controller && ! -L $projected_permission_controller ]] ||
      die 'projected exact PermissionController APK is absent or linked'
    [[ $(sha256sum "$projected_permission_controller" | cut -d' ' -f1) == \
       "$expected_permission_controller_sha" ]] ||
      die 'projected exact PermissionController APK hash mismatch'
    projected_extservices=/proc/$(lxc-info -P "$lxc_path" -n "$name" -pH)/root/system/priv-app/LumaGoogleExtServices/GoogleExtServices.apk
    [[ -f $projected_extservices && ! -L $projected_extservices ]] ||
      die 'projected exact Services Extension APK is absent or linked'
    [[ $(sha256sum "$projected_extservices" | cut -d' ' -f1) == \
       "$expected_extservices_sha" ]] ||
      die 'projected exact Services Extension APK hash mismatch'
    projected_sdk_sandbox=/proc/$(lxc-info -P "$lxc_path" -n "$name" -pH)/root/system/app/LumaSdkSandboxGoogle/SdkSandboxGoogle.apk
    [[ -f $projected_sdk_sandbox && ! -L $projected_sdk_sandbox ]] ||
      die 'projected exact SDK Sandbox APK is absent or linked'
    [[ $(sha256sum "$projected_sdk_sandbox" | cut -d' ' -f1) == \
       "$expected_sdk_sandbox_sha" ]] ||
      die 'projected exact SDK Sandbox APK hash mismatch'
    projected_connectivity_resources=/proc/$(lxc-info -P "$lxc_path" -n "$name" -pH)/root/system/priv-app/LumaNetRes/ServiceConnectivityResourcesGoogle.apk
    [[ -f $projected_connectivity_resources && ! -L $projected_connectivity_resources ]] ||
      die 'projected exact connectivity resources APK is absent or linked'
    [[ $(sha256sum "$projected_connectivity_resources" | cut -d' ' -f1) == \
       "$expected_connectivity_resources_sha" ]] ||
      die 'projected exact connectivity resources APK hash mismatch'
    printf 'active_count=0\npermission_apex=recognized_not_active\npermission_controller_apk=exact_system_projection\nservices_extension_apk=exact_system_projection\nsdk_sandbox_apk=exact_system_projection\nconnectivity_resources_apk=exact_system_projection\nconnectivity_resources_path_filter=container_adapted\n' \
      >"$run_dir/apex-active-state.log"
  else
    [[ $apex_active_count -eq 41 ]] ||
      die "stock apexd active package count differs: $apex_active_count"
    [[ -f /proc/$(lxc-info -P "$lxc_path" -n "$name" -pH)/root/apex/com.android.permission/priv-app/GooglePermissionController@360673420/GooglePermissionController.apk ]] ||
      die 'active permission APEX lacks its exact PermissionController APK'
    printf 'active_count=%s\npermission_apex=active\npermission_controller_apk=present\n' \
      "$apex_active_count" >"$run_dir/apex-active-state.log"
  fi
fi

# AudioService synchronously waits for the native audio Binder APIs while
# system_server boots. Require the exact stock audioserver to publish both APIs
# without receiving a physical ALSA device, and prove the deliberately absent
# GPU service is not crash-looping in this CPU-rendered container.
if [[ $exact_audioserver == true ]]; then
  audioserver_ready=false
  for _ in {1..300}; do
    audioserver_state=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop init.svc.audioserver 2>/dev/null || true)
    audioserver_pid=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/pidof audioserver 2>/dev/null || true)
    vendor_audio_state=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop init.svc.vendor.audio-hal 2>/dev/null || true)
    vendor_audio_pid=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/pidof android.hardware.audio.service_64 2>/dev/null || true)
    audio_services=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/service list 2>/dev/null || true)
    if [[ $audioserver_state == running && $audioserver_pid =~ ^[0-9]+$ &&
          $vendor_audio_state == running && $vendor_audio_pid =~ ^[0-9]+$ ]] &&
       grep -Eq '[[:space:]]media\.audio_flinger:' <<<"$audio_services" &&
       grep -Eq '[[:space:]]media\.audio_policy:' <<<"$audio_services"; then
      audioserver_ready=true
      break
    fi
    sleep 0.1
  done
  init_pid=$(lxc-info -P "$lxc_path" -n "$name" -pH)
  gpu_state=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /system/bin/getprop init.svc.gpu 2>/dev/null || true)
  gpu_pid=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /system/bin/pidof gpuservice 2>/dev/null || true)
  {
    printf 'init.svc.audioserver=%s\n' "$audioserver_state"
    printf 'audioserver_pid=%s\n' "$audioserver_pid"
    printf 'init.svc.vendor.audio-hal=%s\n' "$vendor_audio_state"
    printf 'vendor_audio_hal_pid=%s\n' "$vendor_audio_pid"
    grep -E '[[:space:]]media\.audio_(flinger|policy):' <<<"$audio_services" || true
    printf 'physical_snd_directory=%s\n' "$([[ -e /proc/$init_pid/root/dev/snd ]] && printf present || printf absent)"
    printf 'init.svc.gpu=%s\n' "$gpu_state"
    printf 'gpuservice_pid=%s\n' "$gpu_pid"
  } >"$run_dir/stock-audioserver-state.log"
  [[ $audioserver_ready == true ]] ||
    die 'exact stock audioserver did not publish both native audio Binder APIs'
  [[ ! -e /proc/$init_pid/root/dev/snd ]] ||
    die 'physical sound devices were unexpectedly visible in isolated C1'
  [[ -z $gpu_pid ]] || die 'gpuservice unexpectedly ran in the GPU-free container'
fi

# Optional extension of the same hardware-free construction boundary. Android
# 16 deliberately blocks the zygote trigger until Keystore has delivered the
# exact activated-APEX module inventory to KeyMint. Requiring both that honest
# property, Android's own sys.boot_completed publication, and a stable stock
# system_server proves the Java framework crossed that boundary; no property
# is synthesized by this harness.
if [[ $framework_required == true ]]; then
  framework_ready=false
  framework_stable_samples=0
  framework_stable_pid=
  framework_first_pid=
  for _ in {1..1800}; do
    [[ $(lxc-info -P "$lxc_path" -n "$name" -sH 2>/dev/null || true) == RUNNING ]] ||
      die 'container stopped before Java framework publication'
    module_hash_sent=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop keystore.module_hash.sent 2>/dev/null || true)
    zygote_state=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop init.svc.zygote 2>/dev/null || true)
    system_server_pid=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/pidof system_server 2>/dev/null || true)
    boot_completed=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/getprop sys.boot_completed 2>/dev/null || true)
    if [[ $module_hash_sent == true && $zygote_state == running &&
          $boot_completed == 1 && $system_server_pid =~ ^[0-9]+$ ]]; then
      if [[ -z $framework_first_pid ]]; then
        framework_first_pid=$system_server_pid
      elif [[ $system_server_pid != "$framework_first_pid" ]]; then
        die "system_server restarted during framework gate: $framework_first_pid -> $system_server_pid"
      fi
      if [[ $system_server_pid == "$framework_stable_pid" ]]; then
        framework_stable_samples=$((framework_stable_samples + 1))
      else
        framework_stable_pid=$system_server_pid
        framework_stable_samples=1
      fi
      if (( framework_stable_samples >= framework_stable_samples_required )); then
        framework_ready=true
        break
      fi
    else
      framework_stable_pid=
      framework_stable_samples=0
    fi
    sleep 0.2
  done
  {
    printf 'keystore.module_hash.sent=%s\n' "$module_hash_sent"
    printf 'init.svc.zygote=%s\n' "$zygote_state"
    printf 'sys.boot_completed=%s\n' "$boot_completed"
    printf 'system_server_pid=%s\n' "$system_server_pid"
    printf 'first_system_server_pid=%s\n' "$framework_first_pid"
    printf 'stable_samples=%s\n' "$framework_stable_samples"
    printf 'required_stable_samples=%s\n' "$framework_stable_samples_required"
  } >"$run_dir/framework-state.log"
  lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /system/bin/ps -A -o PID,UID,NAME >"$run_dir/framework-processes.log" 2>&1 || true
  lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /system/bin/service list >"$run_dir/framework-services.log" 2>&1 || true
  [[ $framework_ready == true ]] ||
    die 'Android did not truthfully publish boot completion with a stable system_server'

  phone_pid=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /system/bin/pidof com.android.phone 2>/dev/null || true)
  cp "$run_dir/framework-services.log" "$run_dir/telephony-framework-services.log"
  {
    printf 'com.android.phone_pid=%s\n' "$phone_pid"
    for binder_service in phone telephony.registry isub iphonesubinfo isms carrier_config telecom; do
      if grep -Eq "[[:space:]]${binder_service//./\\.}:" \
           "$run_dir/framework-services.log"; then
        printf '%s=published\n' "$binder_service"
      else
        printf '%s=absent\n' "$binder_service"
      fi
    done
  } >"$run_dir/telephony-framework-state.log"
  [[ $phone_pid =~ ^[0-9]+$ ]] ||
    die 'stock com.android.phone did not start after Android boot completion'
  for binder_service in phone telephony.registry isub iphonesubinfo isms carrier_config telecom; do
    grep -Eq "[[:space:]]${binder_service//./\\.}:" "$run_dir/framework-services.log" ||
      die "stock telephony Binder service is absent: $binder_service"
  done
  if [[ $isolated_netd == true ]]; then
    init_pid=$(lxc-info -P "$lxc_path" -n "$name" -pH)
    host_netns=$(readlink /proc/self/ns/net)
    container_netns=$(readlink "/proc/$init_pid/ns/net")
    host_bpffs_identity=$(stat -c '%d:%i' /sys/fs/bpf)
    container_bpffs_identity=$(stat -c '%d:%i' "/proc/$init_pid/root/sys/fs/bpf")
    container_bpffs_mount=$(awk '
      $5 == "/sys/fs/bpf" {
        for (i = 7; i <= NF; i++) {
          if ($i == "-") {
            print $5, $(i + 2), $(i + 1), $6
            exit
          }
        }
      }
    ' "/proc/$init_pid/mountinfo")
    [[ -n $container_bpffs_mount ]] ||
      die 'stock Android private BPF mount is absent from container mountinfo'
    container_interfaces=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/sh -c "awk -F: 'NR > 2 { gsub(/[[:space:]]/, \"\", \$1); if (length(\$1)) print \$1 }' /proc/net/dev" \
      2>/dev/null | sort -u)
    find /sys/fs/bpf -xdev -mindepth 1 \
      -printf '%P type=%y mode=%m uid=%u gid=%g\n' 2>/dev/null | sort \
      >"$run_dir/host-bpffs-during.log"
    cmp -s "$run_dir/host-bpffs-before.log" "$run_dir/host-bpffs-during.log" ||
      die 'C1 changed Fedora host BPF pins'
    {
      printf 'host_netns=%s\n' "$host_netns"
      printf 'container_netns=%s\n' "$container_netns"
      printf 'interfaces=%s\n' "$(tr '\n' ',' <<<"$container_interfaces" | sed 's/,$//')"
      printf 'host_bpffs_identity=%s\n' "$host_bpffs_identity"
      printf 'container_bpffs_identity=%s\n' "$container_bpffs_identity"
      printf 'container_bpffs_mount=%s\n' "$container_bpffs_mount"
      printf 'bpf.progs_loaded='
      lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/getprop bpf.progs_loaded 2>/dev/null || true
      printf 'allowlist_program='
      if [[ -e /proc/$init_pid/root/sys/fs/bpf/netd_shared/prog_netd_skfilter_allowlist_xtbpf ]]; then
        printf 'present\n'
      else
        printf 'absent\n'
      fi
      printf 'private_pin_count=%s\n' \
        "$(find "/proc/$init_pid/root/sys/fs/bpf" -xdev -mindepth 1 2>/dev/null | wc -l)"
      printf 'init.svc.netd='
      lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/getprop init.svc.netd 2>/dev/null || true
      printf 'pid='
      lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/pidof netd 2>/dev/null || true
      printf 'aidl_service='
      if grep -Fq 'android.system.net.netd.INetd/default' \
         "$run_dir/framework-services.log"; then
        printf 'published\n'
      else
        printf 'absent\n'
      fi
    } >"$run_dir/isolated-netd-state.log"
    [[ $host_netns != "$container_netns" ]] ||
      die 'stock netd shares the Fedora host network namespace'
    [[ $host_bpffs_identity != "$container_bpffs_identity" ]] ||
      die 'stock Android shares Fedora host BPF pins'
    [[ $container_interfaces == lo ]] ||
      die "stock netd can see a non-loopback interface: $container_interfaces"
    grep -Eq '^container_bpffs_mount=/sys/fs/bpf bpf bpf rw,' \
      "$run_dir/isolated-netd-state.log" ||
      die 'stock Android private BPF filesystem is not mounted read-write'
    grep -qx 'bpf.progs_loaded=1' "$run_dir/isolated-netd-state.log" ||
      die 'exact stock Android BPF loader did not complete'
    grep -qx 'allowlist_program=present' "$run_dir/isolated-netd-state.log" ||
      die 'exact stock Android netd allowlist program is absent'
    grep -qx 'init.svc.netd=running' "$run_dir/isolated-netd-state.log" ||
      die 'exact stock netd is not running'
    grep -Eq '^pid=[0-9]+$' "$run_dir/isolated-netd-state.log" ||
      die 'exact stock netd PID is absent'
    grep -qx 'aidl_service=published' "$run_dir/isolated-netd-state.log" ||
      die 'exact stock netd did not publish its AIDL service'
  fi
  if [[ $empty_sensors_hal == true ]]; then
    {
      printf 'init.svc.vendor.sensors-hal-multihal='
      lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/getprop init.svc.vendor.sensors-hal-multihal 2>/dev/null || true
      printf 'aidl_hal=' 
      if grep -Fq 'android.hardware.sensors.ISensors/default' \
         "$run_dir/framework-services.log"; then
        printf 'published\n'
      else
        printf 'absent\n'
      fi
      printf 'native_sensorservice='
      if grep -Eq '(^|[[:space:]])sensorservice:' \
         "$run_dir/framework-services.log"; then
        printf 'published\n'
      else
        printf 'absent\n'
      fi
    } >"$run_dir/empty-sensors-hal-state.log"
    grep -qx 'init.svc.vendor.sensors-hal-multihal=running' \
      "$run_dir/empty-sensors-hal-state.log" ||
      die 'stock zero-sensor Multi-HAL is not running'
    grep -qx 'aidl_hal=published' "$run_dir/empty-sensors-hal-state.log" ||
      die 'stock zero-sensor AIDL HAL did not publish'
    grep -qx 'native_sensorservice=published' "$run_dir/empty-sensors-hal-state.log" ||
      die 'native SensorService did not publish'
  fi
  if [[ $exact_surfaceflinger == true ]]; then
    {
      printf 'init.svc.surfaceflinger='
      lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/getprop init.svc.surfaceflinger 2>/dev/null || true
      printf 'pid='
      lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/pidof surfaceflinger 2>/dev/null || true
      printf 'binder_service='
      if grep -Fq 'SurfaceFlingerAIDL' "$run_dir/framework-services.log"; then
        printf 'published\n'
      else
        printf 'absent\n'
      fi
    } >"$run_dir/surfaceflinger-state.log"
    grep -qx 'init.svc.surfaceflinger=running' "$run_dir/surfaceflinger-state.log" ||
      die 'stock SurfaceFlinger is not running'
    grep -qx 'binder_service=published' "$run_dir/surfaceflinger-state.log" ||
      die 'stock SurfaceFlinger did not publish its Binder API'
  fi
  if [[ $aosp_noop_composer == true ]]; then
    {
      printf 'init.svc.vendor.hwcomposer-3='
      lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/getprop init.svc.vendor.hwcomposer-3 2>/dev/null || true
      printf 'pid='
      lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/pidof android.hardware.graphics.composer3-service.ranchu \
        2>/dev/null || true
      printf 'mode='
      lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/getprop ro.vendor.hwcomposer.mode 2>/dev/null || true
      printf 'display_finder_mode='
      lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/getprop ro.vendor.hwcomposer.display_finder_mode 2>/dev/null || true
    } >"$run_dir/noop-composer-state.log"
    grep -qx 'init.svc.vendor.hwcomposer-3=running' "$run_dir/noop-composer-state.log" ||
      die 'AOSP no-op composer is not running'
    grep -Eq '^pid=[0-9]+$' "$run_dir/noop-composer-state.log" ||
      die 'AOSP no-op composer PID is absent'
    grep -qx 'mode=noop' "$run_dir/noop-composer-state.log" ||
      die 'AOSP composer did not enter no-op composition mode'
    grep -qx 'display_finder_mode=noop' "$run_dir/noop-composer-state.log" ||
      die 'AOSP composer did not enter synthetic-display mode'
  fi
  [[ $framework_ready == true ]] || die 'stock Java framework publication timed out'
  if [[ $permission_controller_projection == true ]]; then
    permission_controller_package_ready=false
    for _ in {1..300}; do
      current_system_server_pid=$(lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/pidof system_server 2>/dev/null || true)
      [[ $current_system_server_pid == "$framework_stable_pid" ]] ||
        die 'system_server restarted before PermissionController publication'
      lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
        /system/bin/cmd package path com.google.android.permissioncontroller \
        >"$run_dir/permission-controller-package.log" 2>&1 || true
      if grep -qx 'package:/system/priv-app/LumaGooglePermissionController/GooglePermissionController.apk' \
         "$run_dir/permission-controller-package.log"; then
        permission_controller_package_ready=true
        break
      fi
      sleep 0.1
    done
    [[ $permission_controller_package_ready == true ]] ||
      die 'PackageManager did not resolve the exact PermissionController package'
    grep -qx 'package:/system/priv-app/LumaGooglePermissionController/GooglePermissionController.apk' \
      "$run_dir/permission-controller-package.log" ||
      die 'PermissionController package resolved from an unexpected path'
    lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/cmd package path com.google.android.ext.services \
      >"$run_dir/services-extension-package.log" 2>&1 ||
      die 'PackageManager did not resolve the exact Services Extension package'
    grep -qx 'package:/system/priv-app/LumaGoogleExtServices/GoogleExtServices.apk' \
      "$run_dir/services-extension-package.log" ||
      die 'Services Extension package resolved from an unexpected path'
    lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
      /system/bin/cmd package path com.google.android.sdksandbox \
      >"$run_dir/sdk-sandbox-package.log" 2>&1 ||
      die 'PackageManager did not resolve the exact SDK Sandbox package'
    grep -qx 'package:/system/app/LumaSdkSandboxGoogle/SdkSandboxGoogle.apk' \
      "$run_dir/sdk-sandbox-package.log" ||
      die 'SDK Sandbox package resolved from an unexpected path'
  fi
fi

init_pid=$(lxc-info -P "$lxc_path" -n "$name" -pH)
[[ $init_pid =~ ^[0-9]+$ ]] || die 'container init PID unavailable'
find "/proc/$init_pid/root/dev" -type b -printf '%p\n' | sort \
  >"$run_dir/visible-block-devices.log"
if [[ $exact_apex_loop_metadata == true ]]; then
  if [[ $permission_controller_projection == true ]]; then
    # Android ueventd removes the read-only loop nodes after apexd has used
    # them to reconstruct its startup database. The only optional steady-state
    # block node is C1's disposable, regular-file-backed FRP loop.
    : >"$run_dir/expected-block-devices.log"
    if [[ $exact_audioserver == true ]]; then
      printf '/proc/%s/root/dev/block/bootdevice/by-name/frp\n' "$init_pid" \
        >>"$run_dir/expected-block-devices.log"
    fi
    cmp -s "$run_dir/expected-block-devices.log" "$run_dir/visible-block-devices.log" ||
      die 'container block-device set differs after APEX reconstruction'
    printf 'startup_verified_loop_nodes=41\nsteady_state_visible_block_nodes=%s\n' \
      "$([[ $exact_audioserver == true ]] && printf 1 || printf 0)" \
      >"$run_dir/apex-loop-lifecycle.log"
  else
    : >"$run_dir/expected-block-devices.log"
    for apex_loop_node in "${apex_loop_nodes[@]}"; do
      printf '/proc/%s/root/dev/block/%s\n' "$init_pid" "${apex_loop_node#/dev/}" \
        >>"$run_dir/expected-block-devices.log"
    done
    if [[ $exact_audioserver == true ]]; then
      printf '/proc/%s/root/dev/block/bootdevice/by-name/frp\n' "$init_pid" \
        >>"$run_dir/expected-block-devices.log"
    fi
    sort -o "$run_dir/expected-block-devices.log" \
      "$run_dir/expected-block-devices.log"
    cmp -s "$run_dir/expected-block-devices.log" "$run_dir/visible-block-devices.log" ||
      die 'container block-device set differs from verified APEX loop set'
  fi
  for apex_loop_node in "${apex_loop_nodes[@]}"; do
    apex_loop_name=${apex_loop_node#/dev/}
    [[ $(cat "/sys/class/block/$apex_loop_name/ro") == 1 ]] ||
      die "visible APEX loop device lost kernel read-only state: $apex_loop_node"
  done
  [[ ! -e /proc/$init_pid/root/dev/loop-control ]] ||
    die 'container exposes loop-control'
  [[ ! -e /proc/$init_pid/root/dev/mapper/control ]] ||
    die 'container exposes device-mapper control'
else
  : >"$run_dir/expected-block-devices.log"
  if [[ $exact_audioserver == true ]]; then
    printf '/proc/%s/root/dev/block/bootdevice/by-name/frp\n' "$init_pid" \
      >>"$run_dir/expected-block-devices.log"
  fi
  cmp -s "$run_dir/expected-block-devices.log" "$run_dir/visible-block-devices.log" ||
    die 'container block-device set differs from the isolated gate'
fi
if [[ $exact_audioserver == true ]]; then
  [[ -b /proc/$init_pid/root/dev/block/bootdevice/by-name/frp ]] ||
    die 'container ephemeral FRP block node is absent'
  [[ $(stat -c '%t:%T' /proc/$init_pid/root/dev/block/bootdevice/by-name/frp) == \
     $(stat -c '%t:%T' "$frp_loop_node") ]] ||
    die 'container FRP node is not the disposable loop device'
  [[ $(cat "/sys/class/block/$frp_loop_name/loop/backing_file") == \
     "$ssd_scratch/frp" ]] || die 'container FRP loop backing file changed'
fi
[[ -c /proc/$init_pid/root/dev/tee0 ]] || die 'container TEE client node is absent'
[[ ! -e /proc/$init_pid/root/dev/dri ]] || die 'container exposes DRM devices'
[[ ! -e /proc/$init_pid/root/dev/kgsl-3d0 ]] || die 'container exposes KGSL'
[[ ! -e /proc/$init_pid/root/dev/graphics ]] || die 'container exposes framebuffer devices'
if [[ $exact_stock_allocator == true ]]; then
  [[ -c /proc/$init_pid/root/dev/dma_heap/system ]] ||
    die 'container system DMA-BUF heap is absent'
  [[ -c /proc/$init_pid/root/dev/dma_heap/qcom,system ]] ||
    die 'container QREL system-heap pathname is absent'
  [[ $(stat -c '%t:%T' /proc/$init_pid/root/dev/dma_heap/system) == \
     $(stat -c '%t:%T' /dev/dma_heap/system) ]] ||
    die 'container system DMA-BUF heap identity differs'
  [[ $(stat -c '%t:%T' /proc/$init_pid/root/dev/dma_heap/qcom,system) == \
     $(stat -c '%t:%T' /dev/dma_heap/system) ]] ||
    die 'container QREL system-heap pathname is not the RAM-only system heap'
  [[ ! -e /proc/$init_pid/root/dev/qti-smmu-proxy ]] ||
    die 'container exposes the Qualcomm SMMU proxy'
fi
[[ -c /proc/$init_pid/root/dev/smcinvoke ]] || die 'container SMCInvoke compatibility name is absent'
[[ $(stat -c '%t:%T' /proc/$init_pid/root/dev/tee0) == \
   $(stat -c '%t:%T' /proc/$init_pid/root/dev/smcinvoke) ]] ||
  die 'SMCInvoke compatibility name is not the QCOMTEE client node'
[[ ! -e /proc/$init_pid/root/dev/teepriv0 ]] || die 'container exposes privileged TEE node'
[[ ! -e /proc/$init_pid/root/dev/bsg/0:0:0:49476 ]] || die 'container exposes RPMB device'
[[ -f /proc/$init_pid/root/dev/block/bootdevice/by-name/ssd &&
   ! -L /proc/$init_pid/root/dev/block/bootdevice/by-name/ssd ]] ||
  die 'container SSD scratch is absent or linked'
[[ $(stat -c '%s' /proc/$init_pid/root/dev/block/bootdevice/by-name/ssd) == 8192 ]] ||
  die 'container SSD scratch size differs'
for binder_node in binder hwbinder vndbinder; do
  [[ -c /proc/$init_pid/root/dev/$binder_node ]] ||
    die "container Binder node is absent: $binder_node"
done
lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
  /system/bin/ps -A -o PID,UID,NAME >"$run_dir/processes.log" 2>&1 || true
check_luma_primary_owners || die 'a primary Luma modem owner changed during stock qseecomd gate'

stop_logcat
if [[ $angle_null_renderer == true ]]; then
  lxc-attach -P "$lxc_path" -n "$name" --clear-env -- \
    /system/bin/setprop persist.graphics.egl '' >/dev/null 2>&1 || true
fi
lxc-stop -P "$lxc_path" -n "$name" -k
container_started=false
install -o root -g root -m 0600 "$config_backup" "$config"
restore_legacy_binder_modes
if [[ $binder_mode == legacy-exclusive ]]; then
  for _ in {1..50}; do
    [[ $(binder_proc_count) -eq 0 ]] && break
    sleep 0.1
  done
  printf 'active_clients_after=%s\n' "$(binder_proc_count)" \
    >>"$run_dir/binder-boundary.log"
  [[ $(binder_proc_count) -eq 0 ]] || die 'legacy Binder clients remain after teardown'
fi
rm -f -- "$override"
override_created=false
if [[ $apex_override_created == true ]]; then
  rm -f -- "$apex_override"
  apex_override_created=false
fi
if [[ $permission_controller_target_created == true ]]; then
  rmdir -- "$permission_controller_target"
  permission_controller_target_created=false
fi
if [[ $extservices_target_created == true ]]; then
  rmdir -- "$extservices_target"
  extservices_target_created=false
fi
if [[ $sdk_sandbox_target_created == true ]]; then
  rmdir -- "$sdk_sandbox_target"
  sdk_sandbox_target_created=false
fi
if [[ $connectivity_resources_target_created == true ]]; then
  rmdir -- "$connectivity_resources_target"
  connectivity_resources_target_created=false
fi
if [[ $permission_controller_allowlist_target_created == true ]]; then
  rm -f -- "$permission_controller_allowlist_target"
  permission_controller_allowlist_target_created=false
fi
if [[ $extservices_allowlist_target_created == true ]]; then
  rm -f -- "$extservices_allowlist_target"
  extservices_allowlist_target_created=false
fi
if [[ -n $frp_loop_node ]]; then
  chmod "$frp_loop_mode" "$frp_loop_node"
  chown "$frp_loop_uid:$frp_loop_gid" "$frp_loop_node"
  losetup -d "$frp_loop_node"
  frp_loop_node=
fi
rm -f -- "$ssd_scratch/ssd" "$ssd_scratch/frp"
rmdir -- "$ssd_scratch"
ssd_scratch_created=false
rm -f -- "$vendor_dmabuf_target"
vendor_dmabuf_created=false
rmdir -- "$vendor_dmabuf_dir"
vendor_dmabuf_dir_created=false
rm -f -- "$system_mink_target"
system_mink_created=false
if [[ $selinux_disabled_created == true ]]; then
  rm -f -- "$selinux_disabled_target"
  selinux_disabled_created=false
fi
if [[ $restorecon_disabled_created == true ]]; then
  rm -f -- "$restorecon_disabled_target"
  restorecon_disabled_created=false
fi
if [[ $angle_null_created == true ]]; then
  rm -f -- "$angle_egl_target" "$angle_gles1_target" "$angle_gles2_target"
  angle_null_created=false
fi
if [[ $angle_probe_created == true ]]; then
  rm -f -- "$angle_probe_target"
  angle_probe_created=false
fi
if [[ $swangle_created == true ]]; then
  rm -f -- "$swangle_egl_target" "$swangle_gles1_target" "$swangle_gles2_target"
  swangle_created=false
fi
if [[ $swangle_probe_created == true ]]; then
  rm -f -- "$swangle_probe_target"
  swangle_probe_created=false
fi
if [[ $noop_composer_created == true ]]; then
  rm -f -- "$noop_composer_target"
  for noop_lib in "${noop_composer_lib_names[@]}"; do
    rm -f -- "$noop_composer_lib_dir_target/$noop_lib"
  done
  rmdir -- "$noop_composer_lib_dir_target"
  noop_composer_created=false
fi
if [[ -n $system_heap_mode ]]; then
  chmod "$system_heap_mode" /dev/dma_heap/system
  system_heap_mode=
fi
if [[ -f $adapter_diagnostic_log ]]; then
  install -o root -g root -m 0600 "$adapter_diagnostic_log" \
    "$run_dir/adapter-metadata.log"
  rm -f -- "$adapter_diagnostic_log"
fi
chmod "$tee_mode" /dev/tee0
tee_mode=
refs=$(awk '$1=="qcomtee" {print $3}' /proc/modules)
[[ ${refs:-x} == 0 ]] || die 'QCOMTEE remains busy after container teardown'
rmmod qcomtee
module_loaded=false
kill "$kernel_follow_pid" 2>/dev/null || true
wait "$kernel_follow_pid" 2>/dev/null || true
kernel_follow_pid=

if grep -Eiq 'kernel panic|Oops:|(^|[[:space:]<])BUG:|hangcheck|GMU.*(timeout|[[:space:]:=_-]fault)|qcomtee.*(panic|fatal|abort|timeout|[[:space:]:=_-]fault)|TEE.*(panic|fatal|abort|timeout|[[:space:]:=_-]fault)|I/O error' \
  "$run_dir/kernel-new.log"; then
  die 'kernel/TEE/GPU/GMU fault observed'
fi
check_luma_primary_owners || die 'a primary Luma modem owner did not survive teardown'
record_luma_owners >"$run_dir/luma-owners-after.log"
result=pass
printf 'C1_STOCK_QSEECOMD_KEYMINT_GATE=true\n'
printf 'C1_STOCK_FRAMEWORK_GATE=%s\n' "$framework_required"
printf 'C1_BOOT_COMPLETED_GATE=%s\n' "$framework_required"
printf 'C1_EXACT_AUDIOSERVER_GATE=%s\n' "$exact_audioserver"
printf 'C1_STOCK_TELEPHONY_FRAMEWORK_GATE=%s\n' "$framework_required"
printf 'C1_SWANGLE_PASTEL_GATE=%s\n' "$swangle_pastel_renderer"
printf 'C1_AOSP_NOOP_COMPOSER_GATE=%s\n' "$aosp_noop_composer"
printf 'C1_EXACT_STOCK_ALLOCATOR_GATE=%s\n' "$exact_stock_allocator"
printf 'C1_EXACT_APEX_LOOP_METADATA_GATE=%s\n' "$exact_apex_loop_metadata"
printf 'C1_PERMISSION_CONTROLLER_PROJECTION=%s\n' "$permission_controller_projection"
printf 'C1_EMPTY_SENSORS_HAL=%s\n' "$empty_sensors_hal"
printf 'C1_ISOLATED_NETD=%s\n' "$isolated_netd"
printf 'READINESS_PUBLISHER=stock_qseecomd\n'
printf 'BINDER_BOUNDARY=%s\n' "$binder_mode"
printf 'PRIVATE_TEE_CLIENT_ONLY=true\n'
printf 'SMCINVOKE_NAME_IS_TEE0_ALIAS=true\n'
printf 'SSD_STORAGE=ephemeral_regular_file_8192\n'
printf 'FRP_STORAGE=ephemeral_regular_file_524288\n'
printf 'PHYSICAL_FRP_EXPOSED=false\n'
printf 'EPHEMERAL_FRP_LOOP_EXPOSED=%s\n' "$exact_audioserver"
printf 'PHYSICAL_SSD_EXPOSED=false\n'
printf 'PRIVILEGED_TEE_NODE_EXPOSED=false\n'
printf 'PHYSICAL_MODEM_AUDIO_GPU_BLOCK_RPMB_EXPOSED=false\n'
printf 'LUMA_PRIMARY_MODEM_OWNERS_REMAINED_ACTIVE=true\n'
printf 'LUMA_IMS_PREEXISTING_STATE_RECORDED=true\n'
printf 'EVIDENCE_DIR=%s\n' "$run_dir"
