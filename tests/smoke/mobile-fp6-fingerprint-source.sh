#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

doc=$repo_root/docs/research/fp6-fingerprint-backend.md

[ "$FP6_FINGERPRINT_VENDOR" = FocalTech ]
[ "$FP6_FINGERPRINT_MODEL" = FT9362 ]
[ "$FP6_FINGERPRINT_OF_COMPATIBLE" = focaltech,fp ]
[ "$FP6_FINGERPRINT_DEVICE" = /dev/focaltech_fp ]
[ "$FP6_FINGERPRINT_PLATFORM_NODE" = soc:focalfp_ft9362 ]
[ "$FP6_FINGERPRINT_VENDOR_COMMIT" = 871d8476127451aac45dc189b5c2043bf54867fd ]
[ "$FP6_FINGERPRINT_FPS_COMMIT" = 0ae06ca1622f1e0e26421853922ab4d771380e1f ]
[ "$FP6_FINGERPRINT_DT_COMMIT" = e54c8cb7d2d1d99026eff5d0d1c858db4739c478 ]
[ "$FP6_FINGERPRINT_POWER_GPIO" = 29 ]
[ "$FP6_FINGERPRINT_RESET_GPIO" = 74 ]
[ "$FP6_FINGERPRINT_IRQ_GPIO" = 75 ]
[ "$FP6_FINGERPRINT_IRQ_TRIGGER" = IRQ_TYPE_EDGE_RISING ]
[ "$FP6_FINGERPRINT_STOCK_MODULE_SHA256" = a32aa9b34f222abbfa5db2ddd2c47f919ca355ef3cee5f59c76465e2a3039b5d ]
[ "$FP6_FINGERPRINT_ANDROID_TEE" = qsee ]
[ "$FP6_FINGERPRINT_FACTORY_PUBLISHED_SHA256" = d27289da00f596632d2e56bc9af31758a7b752f4f9046e44ffa1e0c4c4ec058a ]
[ "$FP6_FINGERPRINT_FACTORY_SIZE" = 3755017703 ]
[ "$FP6_FINGERPRINT_FACTORY_FULL_ARCHIVE_VERIFIED" = false ]
[ "$FP6_FINGERPRINT_NON_HLOS_SIZE" = 185954304 ]
[ "$FP6_FINGERPRINT_NON_HLOS_CRC32" = 8d90e778 ]
[ "$FP6_FINGERPRINT_NON_HLOS_SHA256" = bd0db602a019e5a2b99a582778ee0e4c5db0b4d5aef9d9ba825d9747686d62d9 ]
[ "$FP6_FINGERPRINT_TRUSTLET_NAME" = focal64 ]
[ "$FP6_FINGERPRINT_TRUSTLET_SEGMENT_COUNT" = 10 ]
[ "$FP6_FINGERPRINT_TRUSTLET_BUNDLE_SHA256" = 104792353c21f86efd7eb9a5773acf033431bc7293b18062e817e898b2973df9 ]
[ "$FP6_FINGERPRINT_TRUSTLET_PROVENANCE_COMPLETE" = true ]
[ "$FP6_FINGERPRINT_TRUSTLET_EXTRACTED_OFFLINE" = true ]
[ "$FP6_FINGERPRINT_TRUSTLET_REDISTRIBUTION_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_TRUSTLET_SECURE_WORLD_ACCEPTED" = true ]

[ "$FP6_FINGERPRINT_BOARD_RESOURCES_IDENTIFIED" = true ]
[ "$FP6_FINGERPRINT_QSEECOM_SOURCE_COMPLETE" = true ]
[ "$FP6_FINGERPRINT_QSEECOM_APPLIES_TO_MILOS_7_1_2" = true ]
[ "$FP6_FINGERPRINT_QSEECOM_PATCH_COUNT" = 43 ]
[ "$FP6_FINGERPRINT_QSEECOM_PATCHSET_SHA256" = 1b65c7f49c53f74c11863d4c032b931d59fcc464e869a218fac7ed6535657bcc ]
[ "$FP6_FINGERPRINT_QSEECOM_ENUMERATION_PATCHSET_SHA256" = 9f9e319c0cbd7b5d42e623811f5e6356e1954215ca72a1f7962ea7bbdec570f4 ]
[ "$FP6_FINGERPRINT_QSEECOM_KERNEL_COMMIT" = 662435b853d865d360507f5daee6ff309838c5ca ]
[ "$FP6_FINGERPRINT_QSEECOM_SUPPLICANT_COMMIT" = 36e06680cf7f690fccbdcd07abc2a64c4bb061d8 ]
[ "$FP6_FINGERPRINT_QSEECOM_API_COMMIT" = 73a9f7a89a41c13486e925d6709fdf7a0d9665aa ]
[ "$FP6_FINGERPRINT_QSEECOM_API_HEADER_SHA256" = e5651d4f9d14df485bf95feba61dc67c9089d4c35b0f067a83ca0b34f7648d04 ]
[ "$FP6_FINGERPRINT_ANDROID_NDK_VERSION" = 29.0.14206865 ]
[ "$FP6_FINGERPRINT_ANDROID_NDK_ARCHIVE_SHA1" = 87e2bb7e9be5d6a1c6cdf5ec40dd4e0c6d07c30b ]
[ "$FP6_FINGERPRINT_ANDROID_NDK_ARCHIVE_SHA256" = 4abbbcdc842f3d4879206e9695d52709603e52dd68d3c1fff04b3b5e7a308ecf ]
[ "$FP6_FINGERPRINT_ANDROID_API" = 35 ]
[ "$FP6_FINGERPRINT_SECUREMSM_COMMIT" = 11617d92a2eeb05d31d713c7022cbc0999c63f8e ]
[ "$FP6_FINGERPRINT_LISTENER_ABI_PATCH_SHA256" = 97b5ffa8eee2f8e15fbadc3c0fe91b24e74f5633d5c627fcb9b3ccee587a4bc6 ]
[ "$FP6_FINGERPRINT_FOCALTECH_PROTOCOL_COMPLETE" = false ]
[ "$FP6_FINGERPRINT_KERNEL_VERSION" = 7.1.2 ]
[ "$FP6_FINGERPRINT_KERNEL_ARCHIVE_SHA256" = 6043a062d595e1b4913512c3414387d9a016c4690d8832720651df07276dc78b ]
[ "$FP6_FINGERPRINT_DRIVER_COMMIT" = 80da648332ffb13f3415224058d286e47429eca7 ]
[ "$FP6_FINGERPRINT_DRIVER_ARCHIVE_SHA256" = 4f4bbe30774a1af4cc0242ac45359007dc35a58043c1498557f9a51db22ea6cd ]
[ "$FP6_FINGERPRINT_DRIVER_VERSION" = V3.1.0-20220701 ]
[ "$FP6_FINGERPRINT_GPL_DRIVER_SOURCE_COMPLETE" = true ]
[ "$FP6_FINGERPRINT_DRIVER_APPLIES_TO_MILOS_7_1_2" = true ]

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

sha256_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  else
    shasum -a 256 | awk '{print $1}'
  fi
}

patch_dir=$repo_root/patches/linux-qseecom
patch_count=$(find "$patch_dir" -maxdepth 1 -type f -name '*.patch' | wc -l | tr -d ' ')
[ "$patch_count" -eq "$FP6_FINGERPRINT_QSEECOM_PATCH_COUNT" ]
patchset_sha=$(
  cd "$patch_dir"
  while IFS= read -r patch; do
    printf '%s  %s\n' "$(sha256_file "$patch")" "$patch"
  done < <(find . -maxdepth 1 -type f -name '*.patch' | LC_ALL=C sort) |
    sha256_stdin
)
[ "$patchset_sha" = "$FP6_FINGERPRINT_QSEECOM_PATCHSET_SHA256" ]

driver_patch_dir=$repo_root/patches/linux-milos-fingerprint
driver_patch_count=$(find "$driver_patch_dir" -maxdepth 1 -type f -name '*.patch' | wc -l | tr -d ' ')
[ "$driver_patch_count" -eq "$FP6_FINGERPRINT_DRIVER_PATCH_COUNT" ]
driver_patchset_sha=$(
  cd "$driver_patch_dir"
  while IFS= read -r patch; do
    printf '%s  %s\n' "$(sha256_file "$patch")" "$patch"
  done < <(find . -maxdepth 1 -type f -name '*.patch' | LC_ALL=C sort) |
    sha256_stdin
)
[ "$driver_patchset_sha" = "$FP6_FINGERPRINT_DRIVER_PATCHSET_SHA256" ]
grep -Fq 'V3.1.0-20220701' "$driver_patch_dir/README.md"
grep -Fq 'class_create(FF_DRV_NAME)' "$driver_patch_dir/0002-Input-focaltech-port-control-shim-to-Linux-7.1.patch"

listener_extension=$repo_root/src/fp6-fingerprint-qcomtee/qcomtee-listener-extension.inc
[ -f "$listener_extension" ]
grep -Fq '#include <linux/freezer.h>' "$listener_extension"
grep -Fq 'wait_event_freezable(luma_listener_request_wait' "$listener_extension"
! grep -Fq 'wait_event_killable(luma_listener_request_wait' "$listener_extension"

wake_source=$repo_root/src/fp6-fingerprint-backend/luma_fp6_wake_input.c
backend_makefile=$repo_root/src/fp6-fingerprint-backend/Makefile
[ -f "$wake_source" ]
grep -Fq 'UI_SET_KEYBIT, KEY_WAKEUP' "$wake_source"
grep -Fq 'wake-input: luma-fp6-wake-input' "$backend_makefile"

overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-fingerprint.dtso
[ "$(sha256_file "$overlay")" = "$FP6_FINGERPRINT_DISABLED_OVERLAY_SHA256" ]
grep -Fq 'status = "disabled";' "$overlay"
grep -Fq 'interrupts = <75 IRQ_TYPE_EDGE_RISING>;' "$overlay"
grep -Fq 'focaltech_fp,reset-gpio = <&tlmm 74 0>;' "$overlay"
grep -Fq 'focaltech_fp,vdd-gpio = <&tlmm 29 0>;' "$overlay"

[ "$FP6_FINGERPRINT_OFFLINE_KERNEL_BUNDLE_READY" = true ]
[ "$FP6_FINGERPRINT_OFFLINE_BOOT_CANDIDATE_READY" = true ]
[ "$FP6_FINGERPRINT_QSEECOM_TEE_ENUMERATION_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_SENSOR_ENUMERATION_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_OFFLINE_QSEECOM_BRIDGE_TESTED" = true ]
[ "$FP6_FINGERPRINT_ANDROID_BIONIC_BRIDGE_READY" = true ]
[ "$FP6_FINGERPRINT_QSEECOM_BRIDGE_BYTE_REPRODUCIBLE" = true ]
[ "$FP6_FINGERPRINT_LINUX_KERNEL_CANDIDATE_READY" = true ]
[ "$FP6_FINGERPRINT_SECURE_BACKEND_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_ENROLLMENT_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_AUTHENTICATION_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_PHYSICAL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_MATCH_ON_CHIP" = true ]
[ "$FP6_FINGERPRINT_RAW_IMAGES_RETAINED" = false ]
[ "$FP6_FINGERPRINT_SCREEN_OFF_WAKE_UNLOCK_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_REPEAT_UNLOCK_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_FREEZER_V144_BUILD_REPRODUCIBLE" = true ]
[ "$FP6_FINGERPRINT_FREEZER_V144_PHYSICAL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_REBOOT_PERSISTENCE_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEEP_SUSPEND_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_COLD_BOOT_UNLOCK_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DISPLAY_WAKE_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_GRAPHICAL_START_GATE_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_SERVICE_REARM_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_SERVICE_GRACEFUL_STOP_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V1_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V1_LISTENER_ID" = 10 ]
[ "$FP6_FINGERPRINT_IDENTITY_V1_ERRNO" = 5 ]
[ "$FP6_FINGERPRINT_IDENTITY_V1_TA_LOAD_ATTEMPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V1_CLEANUP_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_QSEECOM_LISTENER_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_ENUMERATION_KERNEL_SHA256" = 4a0c107bcb461adcec92fb880fdaae5c56409c19c355e894c3a8f95f2ac19938 ]
[ "$FP6_FINGERPRINT_ENUMERATION_BOOT_SHA256" = 0eee9398f82fd5a18c4c68295d4119c4f3e3816359d2473e976221231b331f18 ]
[ "$FP6_FINGERPRINT_LISTENER_V2_KERNEL_SHA256" = b554ffbe490cf573153efabee0e075bf60ff00204367ca111fc8bee2014b75b7 ]
[ "$FP6_FINGERPRINT_LISTENER_V2_CONFIG_SHA256" = "$FP6_FINGERPRINT_CANDIDATE_CONFIG_SHA256" ]
[ "$FP6_FINGERPRINT_LISTENER_V2_DTB_SHA256" = "$FP6_FINGERPRINT_CANDIDATE_DTB_SHA256" ]
[ "$FP6_FINGERPRINT_LISTENER_V2_RAMDISK_SHA256" = "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256" ]
[ "$FP6_FINGERPRINT_LISTENER_V2_FOCALTECH_KO_SHA256" = "$FP6_FINGERPRINT_CANDIDATE_FOCALTECH_KO_SHA256" ]
[ "$FP6_FINGERPRINT_LISTENER_V2_QSEECOMTEE_KO_SHA256" = "$FP6_FINGERPRINT_CANDIDATE_QSEECOMTEE_KO_SHA256" ]
[ "$FP6_FINGERPRINT_LISTENER_V2_BOOT_SHA256" = cd9e885b95c5365e3429d0f29e681c2acda409013d6ffa3039db09f2eb4c176a ]
[ "$FP6_FINGERPRINT_LISTENER_V2_OFFLINE_READY" = true ]
[ "$FP6_FINGERPRINT_LISTENER_V2_PHYSICAL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V2_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V2_LISTENER_FS_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V2_LISTENER_GPFS_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V2_LISTENER_FS_ID" = 10 ]
[ "$FP6_FINGERPRINT_IDENTITY_V2_LISTENER_GPFS_ID" = 28672 ]
[ "$FP6_FINGERPRINT_IDENTITY_V2_LOADER_ACQUIRE_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V2_LOADER_ERRNO" = 22 ]
[ "$FP6_FINGERPRINT_IDENTITY_V2_TA_SESSION_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V2_TA_LOAD_REACHED" = unknown ]
[ "$FP6_FINGERPRINT_IDENTITY_V2_CLEANUP_ACCEPTED" = true ]

identity_v2_runner=$repo_root/scripts/mobile/run-fp6-fingerprint-identity-v2-remote.sh
[ -x "$identity_v2_runner" ]
[ "$(sha256_file "$identity_v2_runner")" = "$FP6_FINGERPRINT_IDENTITY_V2_RUNNER_SHA256" ]
grep -Fq 'listeners=10,28672 app=focal64' "$identity_v2_runner"
grep -Fq 'never' "$identity_v2_runner"

loader_diagnostic_patch=$repo_root/patches/qsee-supplicant/0001-app-loader-report-acquisition-boundaries.patch
[ -f "$loader_diagnostic_patch" ]
[ "$(sha256_file "$loader_diagnostic_patch")" = "$FP6_FINGERPRINT_QSEE_LOADER_DIAGNOSTIC_PATCH_SHA256" ]
[ "$FP6_FINGERPRINT_QSEE_LOADER_DIAGNOSTIC_SHA256" = 57955bc42c50ab5fccd27fa7b27d9c3a1e9717cb2e213fc31c8c820f81335900 ]
[ "$FP6_FINGERPRINT_QSEE_LOADER_DIAGNOSTIC_OFFLINE_READY" = true ]
[ "$FP6_FINGERPRINT_QSEE_LOADER_DIAGNOSTIC_PHYSICAL_ACCEPTED" = true ]
grep -Fq 'operation=attach-session' "$loader_diagnostic_patch"
grep -Fq 'operation=load-session' "$loader_diagnostic_patch"

identity_v3_runner=$repo_root/scripts/mobile/run-fp6-fingerprint-identity-v3-remote.sh
[ -x "$identity_v3_runner" ]
[ "$(sha256_file "$identity_v3_runner")" = "$FP6_FINGERPRINT_IDENTITY_V3_RUNNER_SHA256" ]
[ "$FP6_FINGERPRINT_IDENTITY_V3_OFFLINE_READY" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V3_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V3_ATTACH_ERRNO" = 2 ]
[ "$FP6_FINGERPRINT_IDENTITY_V3_LOAD_ERRNO" = 22 ]
[ "$FP6_FINGERPRINT_IDENTITY_V3_REATTACH_ERRNO" = 2 ]
[ "$FP6_FINGERPRINT_IDENTITY_V3_TA_SESSION_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V3_CLEANUP_ACCEPTED" = true ]
grep -Fq 'acquisition_boundary=attach-einval' "$identity_v3_runner"
grep -Fq 'acquisition_boundary=load-einval' "$identity_v3_runner"
grep -Fq 'acquisition_boundary=loaded' "$identity_v3_runner"

grep -Fq 'publishes the complete GPL' "$doc"
grep -Fq '`libfprint`/`fprintd`' "$doc"
grep -Fq 'Raw images' "$doc"
grep -Fq 'Fingerprint works physically and survives a cold boot.' "$doc"
grep -Fq 'wait_event_freezable' "$doc"
grep -Fq 'GPIO 29 powers the sensor' "$doc"
grep -Fq 'qcom-qseecom-tee' "$doc"
grep -Fq 'sensor overlay remains disabled' "$doc"
grep -Fq 'Offline QSEECom client bridge' "$doc"
grep -Fq 'build exports only those' "$doc"
grep -Fq 'Official QREL 16.95.0 trustlet evidence' "$doc"
grep -Fq 'are not approved for' "$doc"

trustlet_stage=$repo_root/scripts/mobile/prepare-fp6-fingerprint-trustlet.sh
[ -x "$trustlet_stage" ]
grep -Fq 'SECURE_WORLD_ACCEPTED=false' "$trustlet_stage"
grep -Fq 'BIOMETRIC_OPERATION_PERFORMED=false' "$trustlet_stage"
grep -Fq 'root/usr/lib/firmware' "$trustlet_stage"

listener_patch=$patch_dir/0042-firmware-qcom-scm-try-modern-listener-registration.patch
[ "$(sha256_file "$listener_patch")" = "$FP6_FINGERPRINT_LISTENER_ABI_PATCH_SHA256" ]
grep -Fq 'TZ_OS_REGISTER_LISTENER_SMCINVOKE_ID' "$listener_patch"
grep -Fq 'status == -EIO' "$listener_patch"
elf64_patch=$patch_dir/0043-soc-qcom-mdt-loader-support-ELF64-contiguous-images.patch
[ "$(sha256_file "$elf64_patch")" = "$FP6_FINGERPRINT_MDT_ELF64_PATCH_SHA256" ]
grep -Fq 'mdt64_header_valid' "$elf64_patch"
grep -Fq 'ELFDATA2LSB' "$elf64_patch"
grep -Fq 'EM_AARCH64' "$elf64_patch"
grep -Fq 'qcom_mdt_get_image_size' "$elf64_patch"
grep -Fq 'qcom_mdt_read_image' "$elf64_patch"
[ "$FP6_FINGERPRINT_MDT_ELF64_SOURCE_SHA256" = 58fcc781833bdf1516b39894c399a32034d54b9f2907c53a03b67145e04e909f ]
[ "$FP6_FINGERPRINT_MDT_ELF64_OBJECT_COMPILED" = true ]
[ "$FP6_FINGERPRINT_MDT_ELF64_KERNEL_SHA256" = 685c1e272846c32f12289442be09cf7b3b2c785dd8a595ff9c139565978a8597 ]
[ "$FP6_FINGERPRINT_MDT_ELF64_CONFIG_SHA256" = "$FP6_FINGERPRINT_LISTENER_V2_CONFIG_SHA256" ]
[ "$FP6_FINGERPRINT_MDT_ELF64_DTB_SHA256" = "$FP6_FINGERPRINT_LISTENER_V2_DTB_SHA256" ]
[ "$FP6_FINGERPRINT_MDT_ELF64_RAMDISK_SHA256" = "$FP6_FINGERPRINT_LISTENER_V2_RAMDISK_SHA256" ]
[ "$FP6_FINGERPRINT_MDT_ELF64_BOOT_SHA256" = 0b8cd15b0b718dea3433a106c96eb141e0ca62d880f547265a25ce919f3bbb3b ]
[ "$FP6_FINGERPRINT_MDT_ELF64_ASSEMBLED_IMAGE_SIZE" = 3595661 ]
[ "$FP6_FINGERPRINT_MDT_ELF64_BUILD_REPRODUCIBLE" = true ]
[ "$FP6_FINGERPRINT_MDT_ELF64_BOOT_CANDIDATE_READY" = true ]
[ "$FP6_FINGERPRINT_MDT_ELF64_PHYSICAL_BOOT_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V4_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V4_LISTENER_FS_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V4_LISTENER_GPFS_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V4_ATTACH_ERRNO" = 2 ]
[ "$FP6_FINGERPRINT_IDENTITY_V4_LOAD_ERRNO" = 12 ]
[ "$FP6_FINGERPRINT_IDENTITY_V4_REATTACH_ERRNO" = 2 ]
[ "$FP6_FINGERPRINT_IDENTITY_V4_TA_SESSION_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V4_CLEANUP_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V4_PAGE_ALLOC_WARNING" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V4_CRITICAL_FAULT" = false ]
[ "$FP6_FINGERPRINT_DMA_CMA_SIZE_MBYTES" = 32 ]
[ "$FP6_FINGERPRINT_DMA_CMA_KERNEL_SHA256" = 1c08af8a27856a87092b47673862f28dff44001c37e9a056c134b93d643f8fbc ]
[ "$FP6_FINGERPRINT_DMA_CMA_CONFIG_SHA256" = fb83e40603ea6567e174fb13f28351560dfd7e83461903723f3fa013b9a7c604 ]
[ "$FP6_FINGERPRINT_DMA_CMA_DTB_SHA256" = "$FP6_FINGERPRINT_LISTENER_V2_DTB_SHA256" ]
[ "$FP6_FINGERPRINT_DMA_CMA_RAMDISK_SHA256" = "$FP6_FINGERPRINT_LISTENER_V2_RAMDISK_SHA256" ]
[ "$FP6_FINGERPRINT_DMA_CMA_BOOT_SHA256" = f0ae4d5288818c57515211a8187ccafe17bf7a12231cf2aa6bd761fd6c0e7e7b ]
[ "$FP6_FINGERPRINT_DMA_CMA_BUILD_REPRODUCIBLE" = true ]
[ "$FP6_FINGERPRINT_DMA_CMA_BOOT_CANDIDATE_READY" = true ]
[ "$FP6_FINGERPRINT_DMA_CMA_PHYSICAL_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V5_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V5_DMA_CMA_RUNTIME_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V5_TA_STAGED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V5_TA_LOAD_ATTEMPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V5_KERNEL_OOPS" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V5_OOPS_SYMBOL" = software_node_notify ]
[ "$FP6_FINGERPRINT_IDENTITY_V5_ROLLBACK_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V5_CLEANUP_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_QSEE_POOL_SIZE_MBYTES" = 16 ]
[ "$FP6_FINGERPRINT_QSEE_POOL_ALIGNMENT_MBYTES" = 4 ]
[ "$FP6_FINGERPRINT_QSEE_POOL_KERNEL_ABI_UNCHANGED" = true ]
[ "$FP6_FINGERPRINT_QSEE_POOL_DTB_SHA256" = 844cbdeae3d43ec776a20f639620568fdabcd02bce039a55dd66c9ac3926f0a5 ]
[ "$FP6_FINGERPRINT_QSEE_POOL_BOOT_SHA256" = 4aa3943a982d91e8b364c55a90df2aadcebae39d0631d74617e0a47d30e3e53d ]
[ "$FP6_FINGERPRINT_QSEE_POOL_BUILD_REPRODUCIBLE" = true ]
[ "$FP6_FINGERPRINT_QSEE_POOL_BOOT_CANDIDATE_READY" = true ]
[ "$FP6_FINGERPRINT_QSEE_POOL_PHYSICAL_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V6_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V6_POOL_RESERVED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V6_POOL_ASSIGNED_TO_SCM" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V6_POOL_DMA_RANGE_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V6_TA_STAGED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V6_TA_LOAD_ATTEMPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V6_CRITICAL_FAULT" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V6_CLEANUP_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_QSEE_POOL_LOW32_ALLOC_START" = 0x80000000 ]
[ "$FP6_FINGERPRINT_QSEE_POOL_LOW32_ALLOC_SIZE" = 0x80000000 ]
[ "$FP6_FINGERPRINT_QSEE_POOL_LOW32_DTB_SHA256" = 971ae7aa5bfb7a2821c65fc9a964ddbc57c2a9ab45065e3bdbedb75da34f0d9a ]
[ "$FP6_FINGERPRINT_QSEE_POOL_LOW32_BOOT_SHA256" = c81af5c9ef8a5821c5d15b067eef1d0076b64d5277a9aa9a3c0969f6e4ff6d08 ]
[ "$FP6_FINGERPRINT_QSEE_POOL_LOW32_BUILD_REPRODUCIBLE" = true ]
[ "$FP6_FINGERPRINT_QSEE_POOL_LOW32_BOOT_CANDIDATE_READY" = true ]
[ "$FP6_FINGERPRINT_QSEE_POOL_LOW32_PHYSICAL_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V7_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V7_POOL_RUNTIME_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V7_LISTENER_FS_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V7_LISTENER_GPFS_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V7_LOAD_ERRNO" = 5 ]
[ "$FP6_FINGERPRINT_IDENTITY_V7_TA_SESSION_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V7_CRITICAL_FAULT" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V7_CLEANUP_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_COMMONLIB64_LOADED_BY_UEFI" = true ]
[ "$FP6_FINGERPRINT_STOCK_COMMONLIB64_PRESENT_IN_NON_HLOS" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V7_REJECTION_CAUSE_IDENTIFIED" = false ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_PATCH_COUNT" = 1 ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_PATCHSET_SHA256" = d1dcf0bd0158fc9faf43ce0c9af3bd705dd4836ea53aad6de506406c66f8ba3e ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_DIAGNOSTIC_SOURCE_SHA256" = 2ce753e95289d1795d5aabbfca31f3ad59b75d5364ff273d4b3fbcef7789afc2 ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_KERNEL_SHA256" = 52450e5a464acbe49b2d7f049ad58ccfac631118358935d9039dcdc6de66dacb ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_CONFIG_SHA256" = a403b3cb247d3f2ce358f8debcc3b93d3c1949813488da8e12ac81540cee519c ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_BOOT_SHA256" = 21663b13539a6a347b11d298c629771620e3b964545770fb658c1ff446c0a54e ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_COMPOSER_SHA256" = 5d90abe763cc73660549efe51b7b2bc7dd7f7f3eb06bfbac62b36e8c60929ecc ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_VERIFIER_SHA256" = 722cccd54d3c2f9803528d7579261d15e75a719d349e47015090ffaa162bc220 ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_BUILD_REPRODUCIBLE" = true ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_BOOT_CANDIDATE_READY" = true ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_PHYSICAL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_RUNTIME_CONFIG_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_MODULES_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_LOW32_POOL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_INITIALIZATION_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_CRITICAL_FAULT" = false ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_TA_LOAD_ATTEMPTED" = false ]
[ "$FP6_FINGERPRINT_SHMBRIDGE_IDENTITY_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_RUNNER_SHA256" = cffcd040e2d90ff8ebdffbb47926bafc92485b19920266c44b8eb29116cd8d35 ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_LISTENER_FS_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_LISTENER_GPFS_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_SHMBRIDGE_ALLOCATION_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_SHMBRIDGE_ALLOCATION_SIZE" = 7790592 ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_MDT_LENGTH" = 4752 ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_IMAGE_LENGTH" = 3595661 ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_LOAD_ERRNO" = 5 ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_SCM_RESULT" = 0 ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_SCM_RESPONSE_TYPE" = 0 ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_SCM_RESPONSE_DATA" = 0 ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_TA_SESSION_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_CRITICAL_FAULT" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_CLEANUP_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V8_REJECTION_CAUSE_IDENTIFIED" = false ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_PATCH_COUNT" = 1 ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_PATCHSET_SHA256" = b6f449e4932e12cdb9e85241a85a6656b0911a656ee3f1b4fb500a089274f64d ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_SOURCE_SHA256" = 519b15bea5fb7dc42cd29fa31fcb731c42883e68ffb135909c3f10dbd1e23c6e ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_OVERLAY_SHA256" = 1fd698463d077c869ad4e9d438d5fff60eb325274762c4306418402d7d6871dc ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_SHA256" = 667aa0738c5d91e2bf0dfd4c635b827e49567a511e2e6fa4305002f81af76246 ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_CONFIG_SHA256" = a403b3cb247d3f2ce358f8debcc3b93d3c1949813488da8e12ac81540cee519c ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DTB_SHA256" = ffb598e2ebec2bdc029c3a93e8f666e3a13dc920ea671df93f4c61da8fa6fac2 ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_BOOT_SHA256" = 5d808420b5827264c007f07fea56bc8e0a6dbb41ee22799d7ea184e034b15fa8 ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_BOOT_SIZE" = 27512832 ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_BUILDER_SHA256" = 6949e0fc04b7e4ed744c04c2e5227f48a2c59414e53f2b2865a29379832d879f ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_COMPOSER_SHA256" = 78b3efe866cfd0801f6e850a45651df502c13927e0f30c2e68ddd2100fd1c9de ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_VERIFIER_SHA256" = f5e40162dd7d76a102ed7a2343a199e3ff02e7e94cb62f02f175de0d093b2f98 ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_BUILD_REPRODUCIBLE" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_BOOT_CANDIDATE_READY" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_PHYSICAL_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_BOOT_HASH_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_RUNTIME_CONFIG_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_MODULES_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_TA_POOL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_APPS_POOL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_TA_BRIDGE_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_APPS_BRIDGE_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_GMU_TIMEOUT_COUNT" = 14 ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_CRITICAL_FAULT" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_PHYSICAL_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_TA_LOAD_ATTEMPTED" = false ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_ROLLBACK_BOOT_SHA256" = 21663b13539a6a347b11d298c629771620e3b964545770fb658c1ff446c0a54e ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_ROLLBACK_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_ISOLATION_COMPOSER_SHA256" = c19570a2b11efabdc2382226fc86b44ff513946dcdd56eb8ee966fe7836d23bd ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_BOOT_SHA256" = 432e30cdac2edf181ce94c548c13c35107205baee089d1150126f773df05c666 ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_BOOT_SIZE" = 27512832 ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_DTB_SHA256" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_DTB_SHA256" ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_REPRODUCIBLE" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_PHYSICAL_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_BOOT_HASH_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_RUNTIME_CONFIG_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_MODULES_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_DTB_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_TA_POOL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_WHOLE_POOL_PATHS_ACTIVE" = false ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_CRITICAL_FAULT" = false ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_FAILED_UNITS" = 0 ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_ONLY_PHYSICAL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_BOOT_SHA256" = 9320eee4c5e35d1751cb8fbb1c476132078449d94eeddadddff67716d09ca6f8 ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_BOOT_SIZE" = 27516928 ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_DTB_SHA256" = "$FP6_FINGERPRINT_STOCK_HEAPS_DTB_SHA256" ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_REPRODUCIBLE" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_PHYSICAL_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_BOOT_HASH_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_RUNTIME_CONFIG_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_MODULES_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_TA_POOL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_APPS_POOL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_WHOLE_POOL_PATHS_ACTIVE" = false ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_CRITICAL_FAULT" = false ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_FAILED_UNITS" = 0 ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_DT_ONLY_PHYSICAL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_ISOLATION_TA_LOAD_ATTEMPTED" = false ]
[ "$FP6_FINGERPRINT_STOCK_HEAPS_ACTIVATION_CAUSES_GMU_FAULT" = true ]
grep -Fq 'IDENTITY_ACCEPTED=false' "$doc"
grep -Fq 'cd9e885b95c5365e3429d0f29e681c2acda409013d6ffa3039db09f2eb4c176a' "$doc"
grep -Fq 'software_node_notify()' "$doc"
grep -Fq '4aa3943a982d91e8b364c55a90df2aadcebae39d0631d74617e0a47d30e3e53d' "$doc"
grep -Fq '0x00000009ff000000..0x00000009ffffffff' "$doc"
grep -Fq 'c81af5c9ef8a5821c5d15b067eef1d0076b64d5277a9aa9a3c0969f6e4ff6d08' "$doc"
grep -Fq '`qcom,commonlib64-loaded-by-uefi`' "$doc"
grep -Fq '`CONFIG_QCOM_TZMEM_MODE_GENERIC`' "$doc"
grep -Fq '`QCOM_SCM_ERROR`, mapped to `EIO`' "$doc"
grep -Fq '## SHM-Bridge v7 — physically accepted boot-only candidate' "$doc"
grep -Fq '21663b13539a6a347b11d298c629771620e3b964545770fb658c1ff446c0a54e' "$doc"
grep -Fq 'zero bounded TA-load events' "$doc"
grep -Fq '## Secure-backend identity-v8 — SHM allocation accepted, SCM transport rejected' "$doc"
grep -Fq '7,790,592-byte' "$doc"
grep -Fq '## Stock-heaps v8 — reproducible boot-only candidate' "$doc"
grep -Fq 'Physical result: heap topology accepted, candidate rejected' "$doc"
grep -Fq 'fourteen' "$doc"
grep -Fq '### V9 isolation candidates' "$doc"
grep -Fq '5d808420b5827264c007f07fea56bc8e0a6dbb41ee22799d7ea184e034b15fa8' "$doc"
grep -Fq 'skips APP_REGION_NOTIFICATION' "$doc"

kernel_builder=$repo_root/scripts/mobile/build-fp6-fingerprint-candidate.sh
boot_builder=$repo_root/scripts/mobile/prepare-fp6-fingerprint-boot-candidate.sh
grep -Fq '0042-firmware-qcom-scm-try-modern-listener-registration.patch' "$kernel_builder"
grep -Fq '0043-soc-qcom-mdt-loader-support-ELF64-contiguous-images.patch' "$kernel_builder"
grep -Fq '.luma-fingerprint-qsee-patchset' "$kernel_builder"
grep -Fq 'LUMA_FINGERPRINT_TZMEM_MODE' "$kernel_builder"
grep -Fq 'QCOM_TZMEM_MODE_SHMBRIDGE' "$kernel_builder"
grep -Fq 'FP6_FINGERPRINT_ENUMERATION_KERNEL_SHA256' "$kernel_builder"
grep -Fq 'scripts/config --disable DMA_CMA' "$kernel_builder"
grep -Fq "grep -qx '# CONFIG_DMA_CMA is not set' .config" "$kernel_builder"
! grep -Fq 'scripts/config --enable DMA_CMA' "$kernel_builder"

shmbridge_composer=$repo_root/scripts/mobile/prepare-fp6-fingerprint-shmbridge-candidate.sh
shmbridge_verifier=$repo_root/scripts/mobile/verify-fp6-fingerprint-shmbridge-candidate.sh
[ -x "$shmbridge_composer" ]
[ -x "$shmbridge_verifier" ]
[ "$(sha256_file "$shmbridge_composer")" = "$FP6_FINGERPRINT_SHMBRIDGE_COMPOSER_SHA256" ]
[ "$(sha256_file "$shmbridge_verifier")" = "$FP6_FINGERPRINT_SHMBRIDGE_VERIFIER_SHA256" ]
grep -Fq 'TZMEM_MODE=shmbridge' "$shmbridge_composer"
grep -Fq 'TRUSTLET_LOAD_ENABLED=false' "$shmbridge_composer"
grep -Fq 'PHONE_ACCESSED=false' "$shmbridge_composer"
grep -Fq 'cmp "$work/base/ramdisk" "$work/candidate/ramdisk"' "$shmbridge_verifier"
grep -Fq 'cmp "$work/base/dtb" "$work/candidate/dtb"' "$shmbridge_verifier"
! grep -Eq '(fastboot|ssh |/dev/focaltech_fp|qsee-app-loader)' "$shmbridge_verifier"

identity_v8_runner=$repo_root/scripts/mobile/run-fp6-fingerprint-identity-v8-remote.sh
[ -x "$identity_v8_runner" ]
[ "$(sha256_file "$identity_v8_runner")" = "$FP6_FINGERPRINT_IDENTITY_V8_RUNNER_SHA256" ]
grep -Fq 'expected_boot_sha=21663b13539a6a347b11d298c629771620e3b964545770fb658c1ff446c0a54e' "$identity_v8_runner"
grep -Fq 'expected_config_sha=a403b3cb247d3f2ce358f8debcc3b93d3c1949813488da8e12ac81540cee519c' "$identity_v8_runner"
grep -Fq 'case $listener_id in' "$identity_v8_runner"
grep -Fq 'qsee-app-loader" focal64' "$identity_v8_runner"
! grep -Eq '(fastboot|reboot|fingerprint enroll|fingerprint auth)' "$identity_v8_runner"

stock_heaps_patch=$repo_root/patches/linux-qseecom-stock-heaps/0001-firmware-qcom-register-stock-qseecom-heaps.patch
stock_heaps_overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-stock-heaps.dtso
stock_heaps_composer=$repo_root/scripts/mobile/prepare-fp6-fingerprint-stock-heaps-candidate.sh
stock_heaps_verifier=$repo_root/scripts/mobile/verify-fp6-fingerprint-stock-heaps-candidate.sh
stock_heaps_isolation_composer=$repo_root/scripts/mobile/prepare-fp6-fingerprint-stock-heaps-isolation-candidate.sh
[ "$(sha256_file "$kernel_builder")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_BUILDER_SHA256" ]
[ "$(sha256_file "$stock_heaps_patch")" = "$FP6_FINGERPRINT_STOCK_HEAPS_SOURCE_SHA256" ]
[ "$(sha256_file "$stock_heaps_overlay")" = "$FP6_FINGERPRINT_STOCK_HEAPS_OVERLAY_SHA256" ]
[ "$(sha256_file "$stock_heaps_composer")" = "$FP6_FINGERPRINT_STOCK_HEAPS_COMPOSER_SHA256" ]
[ "$(sha256_file "$stock_heaps_verifier")" = "$FP6_FINGERPRINT_STOCK_HEAPS_VERIFIER_SHA256" ]
[ -x "$stock_heaps_isolation_composer" ]
[ "$(sha256_file "$stock_heaps_isolation_composer")" = "$FP6_FINGERPRINT_STOCK_HEAPS_ISOLATION_COMPOSER_SHA256" ]
[ -x "$stock_heaps_composer" ]
[ -x "$stock_heaps_verifier" ]
grep -Fq 'qcom,tzmem-whole-pool-shmbridge' "$stock_heaps_overlay"
grep -Fq 'qcom,appsbl-qseecom-support' "$stock_heaps_overlay"
grep -Fq 'APP_REGION_NOTIFICATION=false' "$stock_heaps_composer"
grep -Fq 'TRUSTLET_LOAD_ENABLED=false' "$stock_heaps_composer"
grep -Fq 'TA_LOAD_ATTEMPTED=false' "$stock_heaps_composer"

dedicated_heaps_dir=$repo_root/patches/linux-qseecom-dedicated-heaps
dedicated_heaps_patch=$dedicated_heaps_dir/0001-tee-qseecom-isolate-stock-heaps-from-global-SCM.patch
dedicated_heaps_overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-dedicated-heaps.dtso
dedicated_heaps_composer=$repo_root/scripts/mobile/prepare-fp6-fingerprint-dedicated-heaps-candidate.sh
dedicated_heaps_verifier=$repo_root/scripts/mobile/verify-fp6-fingerprint-dedicated-heaps-candidate.sh
dedicated_heaps_module_runner=$repo_root/scripts/mobile/run-fp6-fingerprint-dedicated-heaps-module-v10-remote.sh
identity_v9_runner=$repo_root/scripts/mobile/run-fp6-fingerprint-identity-v9-remote.sh
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_PATCH_COUNT" = 3 ]
[ "$(sha256_file "$dedicated_heaps_patch")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_PATCH_SHA256" ]
dedicated_heaps_patchset_sha=$(
  cd "$dedicated_heaps_dir"
  while IFS= read -r patch; do
    printf '%s  %s\n' "$(sha256_file "$patch")" "$patch"
  done < <(find . -maxdepth 1 -type f -name '*.patch' | LC_ALL=C sort) |
    sha256_stdin
)
[ "$dedicated_heaps_patchset_sha" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_PATCHSET_SHA256" ]
[ "$(sha256_file "$dedicated_heaps_overlay")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_OVERLAY_SHA256" ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_OBJECT_COMPILE_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_FULL_BUILD_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_BUILD_REPRODUCIBLE" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_CONFIG_SHA256" = "$FP6_FINGERPRINT_SHMBRIDGE_CONFIG_SHA256" ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_FOCALTECH_KO_SHA256" = "$FP6_FINGERPRINT_LISTENER_V2_FOCALTECH_KO_SHA256" ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_QSEECOMTEE_KO_SHA256" != "$FP6_FINGERPRINT_LISTENER_V2_QSEECOMTEE_KO_SHA256" ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_COMPOSITION_REPRODUCIBLE" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_BOOT_CANDIDATE_READY" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_QSEECOM_MODULE_INSTALL_REQUIRED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_MODULE_ACTIVATION_READY" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_MODULE_ACTIVATION_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_MODULE_HASH_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_TA_BRIDGE_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_APPS_BRIDGE_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_INITIAL_POOL_ALLOCATION_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_TEE_NODES_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_MODULE_UNLOAD_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_MODULE_CLEANUP_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_MODULE_GATE_CRITICAL_FAULT" = false ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_MODULE_GATE_FAILED_UNITS" = 0 ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_MODULE_PHYSICAL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V9_READY" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V9_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V9_LISTENER_FS_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V9_LISTENER_GPFS_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V9_SHMBRIDGE_TA_HEAP_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V9_SHMBRIDGE_APPS_HEAP_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V9_LOAD_ERRNO" = 5 ]
[ "$FP6_FINGERPRINT_IDENTITY_V9_TA_SESSION_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V9_CRITICAL_FAULT" = false ]
[ "$FP6_FINGERPRINT_IDENTITY_V9_CLEANUP_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_IDENTITY_V9_REJECTION_CAUSE_IDENTIFIED" = false ]
[ -x "$dedicated_heaps_module_runner" ]
[ "$(sha256_file "$dedicated_heaps_module_runner")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_MODULE_RUNNER_SHA256" ]
[ -x "$identity_v9_runner" ]
[ "$(sha256_file "$identity_v9_runner")" = "$FP6_FINGERPRINT_IDENTITY_V9_RUNNER_SHA256" ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_PHYSICAL_ATTEMPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_BOOT_HASH_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_RUNTIME_CONFIG_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_MODULES_UNCHANGED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_DTB_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_TA_POOL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_APPS_POOL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_GLOBAL_SCM_POOL_ACTIVE" = false ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_QSEE_CHILD_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_QSEECOM_MODULE_LOADED" = false ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_CRITICAL_FAULT" = false ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_FAILED_UNITS" = 0 ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_BOOT_PHYSICAL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_TA_LOAD_ATTEMPTED" = false ]
grep -Fq 'qcom_tzmem_pool_new_on_device' "$dedicated_heaps_patch"
grep -Fq 'dedicated QSEECOM heaps active' "$dedicated_heaps_patch"
grep -Fq 'qseecom_tee_configure_tzmem_pool(qtee, &pool_config, true)' "$dedicated_heaps_patch"
! grep -Fq '/delete-property/ memory-region;' "$dedicated_heaps_overlay"
grep -Fq 'compatible = "luma,qseecom-tee-heaps";' "$dedicated_heaps_overlay"
grep -Fq 'memory-region-names = "ta", "apps";' "$dedicated_heaps_overlay"
grep -Fq 'The separately authorized v10 boot-only physical gate passed.' "$doc"
grep -Fq 'bc12893e0403245e2ea4a1cb6a4801c1efdba515d6f726be18f2e55f1752ab33' "$doc"
grep -Fq 'expected_module_sha=126074584721781d038312eb2d0129cd9d6044ee1b54523999a69824d54b7f5e' "$dedicated_heaps_module_runner"
grep -Fq 'insmod "$module"' "$dedicated_heaps_module_runner"
grep -Fq 'rmmod qseecomtee' "$dedicated_heaps_module_runner"
grep -Fq 'trustlet_loaded=false biometric_commands=0' "$dedicated_heaps_module_runner"
! grep -Eq '(qsee-app-loader|qsee-supplicant|focal64|fastboot|reboot)' "$dedicated_heaps_module_runner"
grep -Fq 'expected_boot_sha=bc12893e0403245e2ea4a1cb6a4801c1efdba515d6f726be18f2e55f1752ab33' "$identity_v9_runner"
grep -Fq 'expected_module_sha=126074584721781d038312eb2d0129cd9d6044ee1b54523999a69824d54b7f5e' "$identity_v9_runner"
grep -Fq 'qsee-app-loader" focal64' "$identity_v9_runner"
grep -Fq 'case $listener_id in' "$identity_v9_runner"
grep -Fq 'biometric_commands=0' "$identity_v9_runner"
! grep -Eq '(fastboot|reboot|fingerprint enroll|fingerprint auth)' "$identity_v9_runner"
grep -Fq 'LUMA_FINGERPRINT_DEDICATED_HEAPS' "$kernel_builder"
grep -Fq 'DEDICATED_HEAPS_ENABLED=%s' "$kernel_builder"
[ -x "$dedicated_heaps_composer" ]
[ -x "$dedicated_heaps_verifier" ]
[ "$(sha256_file "$dedicated_heaps_composer")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_COMPOSER_SHA256" ]
[ "$(sha256_file "$dedicated_heaps_verifier")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_VERIFIER_SHA256" ]
grep -Fq 'fdtput -d "$detached_dtb" /firmware/scm memory-region' "$dedicated_heaps_composer"
grep -Fq 'GLOBAL_SCM_MEMORY_REGION_DELETED_BY_COMPOSER=true' "$dedicated_heaps_composer"
grep -Fq 'QSEECOM_MODULE_INSTALL_REQUIRED=true' "$dedicated_heaps_composer"
! grep -Eq '(fastboot|ssh |qsee-app-loader|focal64)' "$dedicated_heaps_verifier"
grep -Fq 'PHONE_ACCESSED=false' "$stock_heaps_composer"
! grep -Eq '(fastboot|ssh |qsee-app-loader|focal64)' "$stock_heaps_verifier"

qseelog_dir=$repo_root/patches/linux-qseecom-qseelog
qseelog_patch=$qseelog_dir/0001-firmware-qcom-scm-add-opt-in-qsee-log-buffer.patch
qseelog_overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-log.dtso
qseelog_composer=$repo_root/scripts/mobile/prepare-fp6-fingerprint-qseelog-candidate.sh
qseelog_verifier=$repo_root/scripts/mobile/verify-fp6-fingerprint-qseelog-candidate.sh
qseelog_decoder=$repo_root/scripts/mobile/decode-fp6-qsee-log.py
[ "$FP6_FINGERPRINT_QSEELOG_PATCH_COUNT" = 1 ]
[ "$(sha256_file "$qseelog_patch")" = "$FP6_FINGERPRINT_QSEELOG_PATCH_SHA256" ]
qseelog_patchset_sha=$(
  cd "$qseelog_dir"
  while IFS= read -r patch; do
    printf '%s  %s\n' "$(sha256_file "$patch")" "$patch"
  done < <(find . -maxdepth 1 -type f -name '*.patch' | LC_ALL=C sort) |
    sha256_stdin
)
[ "$qseelog_patchset_sha" = "$FP6_FINGERPRINT_QSEELOG_PATCHSET_SHA256" ]
[ "$(sha256_file "$qseelog_overlay")" = "$FP6_FINGERPRINT_QSEELOG_OVERLAY_SHA256" ]
[ "$(sha256_file "$qseelog_composer")" = "$FP6_FINGERPRINT_QSEELOG_COMPOSER_SHA256" ]
[ -x "$qseelog_verifier" ]
[ "$(sha256_file "$qseelog_verifier")" = "$FP6_FINGERPRINT_QSEELOG_VERIFIER_SHA256" ]
[ "$(sha256_file "$qseelog_decoder")" = "$FP6_FINGERPRINT_QSEELOG_DECODER_SHA256" ]
[ -x "$qseelog_composer" ]
[ -x "$qseelog_decoder" ]
grep -Fq 'luma,qsee-log-diagnostic' "$qseelog_patch"
grep -Fq 'attr.mode = 0400' "$qseelog_patch"
grep -Fq 'QCOM_SCM_ARGS(2, QCOM_SCM_RW, QCOM_SCM_VAL)' "$qseelog_patch"
grep -Fq 'TRUSTLET_LOAD_ENABLED=false' "$qseelog_composer"
grep -Fq 'TA_LOAD_ATTEMPTED=false' "$qseelog_composer"
! grep -Eq '(fastboot|ssh |qsee-app-loader|focal64)' "$qseelog_composer"
! grep -Eq '(fastboot|ssh |qsee-app-loader|focal64)' "$qseelog_verifier"
grep -Fq 'luma,qsee-log-diagnostic' "$qseelog_verifier"
grep -Fq 'expected exactly {QSEE_LOG_SIZE} bytes' "$qseelog_decoder"

grep -Fq 'boot-fp6-luma-fingerprint-loader-elf64-v3.img' "$boot_builder"
grep -Fq 'FP6_FINGERPRINT_LISTENER_V2_BOOT_SHA256' "$boot_builder"
grep -Fq 'DTB_UNCHANGED=true' "$boot_builder"
grep -Fq 'TRUSTLET_LOAD_ENABLED=false' "$boot_builder"
grep -Fq 'TA_LOAD_ATTEMPTED=false' "$boot_builder"

elf64_verifier=$repo_root/scripts/mobile/verify-fp6-fingerprint-elf64-candidate.sh
[ -x "$elf64_verifier" ]
[ "$(sha256_file "$elf64_verifier")" = "$FP6_FINGERPRINT_MDT_ELF64_VERIFIER_SHA256" ]
grep -Fq 'FP6_FINGERPRINT_MDT_ELF64_BOOT_SHA256' "$elf64_verifier"
grep -Fq 'FP6_FINGERPRINT_LISTENER_V2_BOOT_SHA256' "$elf64_verifier"
grep -Fq 'cmp "$work/base/dtb" "$work/candidate/dtb"' "$elf64_verifier"
grep -Fq 'cmp "$work/base/ramdisk" "$work/candidate/ramdisk"' "$elf64_verifier"
! grep -Eq '(fastboot|ssh |/dev/focaltech_fp|qsee-app-loader)' "$elf64_verifier"

dma_cma_verifier=$repo_root/scripts/mobile/verify-fp6-fingerprint-dma-cma-candidate.sh
[ -x "$dma_cma_verifier" ]
[ "$(sha256_file "$dma_cma_verifier")" = "$FP6_FINGERPRINT_DMA_CMA_VERIFIER_SHA256" ]
grep -Fq 'FP6_FINGERPRINT_DMA_CMA_BOOT_SHA256' "$dma_cma_verifier"
grep -Fq 'CONFIG_DMA_CMA=y' "$dma_cma_verifier"
grep -Fq 'CONFIG_CMA_SIZE_MBYTES=' "$dma_cma_verifier"
grep -Fq 'cmp "$work/base/dtb" "$work/candidate/dtb"' "$dma_cma_verifier"
grep -Fq 'cmp "$work/base/ramdisk" "$work/candidate/ramdisk"' "$dma_cma_verifier"
! grep -Eq '(fastboot|ssh |/dev/focaltech_fp|qsee-app-loader)' "$dma_cma_verifier"

qsee_pool_v5_overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-pool-v5.dtso
[ -f "$qsee_pool_v5_overlay" ]
[ "$(sha256_file "$qsee_pool_v5_overlay")" = "$FP6_FINGERPRINT_QSEE_POOL_OVERLAY_SHA256" ]
! grep -Fq 'alloc-ranges' "$qsee_pool_v5_overlay"

qsee_pool_overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-pool.dtso
[ -f "$qsee_pool_overlay" ]
[ "$(sha256_file "$qsee_pool_overlay")" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_OVERLAY_SHA256" ]
grep -Fq 'compatible = "shared-dma-pool";' "$qsee_pool_overlay"
grep -Fq 'size = <0x0 0x01000000>;' "$qsee_pool_overlay"
grep -Fq 'alignment = <0x0 0x00400000>;' "$qsee_pool_overlay"
grep -Fq 'alloc-ranges = <0x0 0x80000000 0x0 0x80000000>;' "$qsee_pool_overlay"
grep -Fq 'no-map;' "$qsee_pool_overlay"
grep -Fq '&{/firmware/scm}' "$qsee_pool_overlay"

qsee_pool_composer=$repo_root/scripts/mobile/prepare-fp6-fingerprint-qsee-pool-candidate.sh
[ -x "$qsee_pool_composer" ]
[ "$(sha256_file "$qsee_pool_composer")" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_COMPOSER_SHA256" ]
grep -Fq 'FP6_FINGERPRINT_MDT_ELF64_BOOT_SHA256' "$qsee_pool_composer"
grep -Fq 'cmp "$work/base/kernel" "$work/candidate/kernel"' "$qsee_pool_composer"
grep -Fq 'cmp "$work/base/ramdisk" "$work/candidate/ramdisk"' "$qsee_pool_composer"
grep -Fq 'MODULE_ABI_UNCHANGED=true' "$qsee_pool_composer"
grep -Fq 'PHONE_ACCESSED=false' "$qsee_pool_composer"
! grep -Eq '(fastboot|ssh |/dev/focaltech_fp|qsee-app-loader)' "$qsee_pool_composer"

qsee_pool_verifier=$repo_root/scripts/mobile/verify-fp6-fingerprint-qsee-pool-candidate.sh
[ -x "$qsee_pool_verifier" ]
[ "$(sha256_file "$qsee_pool_verifier")" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_VERIFIER_SHA256" ]
grep -Fq 'FP6_FINGERPRINT_QSEE_POOL_LOW32_BOOT_SHA256' "$qsee_pool_verifier"
grep -Fq 'cmp "$work/base/kernel" "$work/candidate/kernel"' "$qsee_pool_verifier"
grep -Fq 'cmp "$work/base/ramdisk" "$work/candidate/ramdisk"' "$qsee_pool_verifier"
grep -Fq '/reserved-memory/qseecom-ta-pool' "$qsee_pool_verifier"
grep -Fq '/firmware/scm memory-region' "$qsee_pool_verifier"
grep -Fq "'0 80000000 0 80000000'" "$qsee_pool_verifier"
! grep -Eq '(fastboot|ssh |/dev/focaltech_fp|qsee-app-loader)' "$qsee_pool_verifier"

[ "$FP6_FINGERPRINT_APPSBL_CONTRACT_PHYSICAL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_APPSBL_CONTRACT_CRITICAL_FAULT" = false ]
[ "$FP6_FINGERPRINT_STOCK_NODE_NAMES_PHYSICAL_ACCEPTED" = true ]
[ "$FP6_FINGERPRINT_STOCK_NODE_NAMES_CRITICAL_FAULT" = false ]
[ "$FP6_FINGERPRINT_STOCK_NODE_NAMES_IDENTITY_LOAD_ERRNO" = 5 ]
[ "$FP6_FINGERPRINT_QSEE_REGION_PROBE_RAW_A0" = 4 ]
[ "$FP6_FINGERPRINT_QSEE_REGION_PROBE_ACCEPTED" = false ]
[ "$FP6_FINGERPRINT_QSEE_TRANSPORT_FAILURE_CONFIRMED" = true ]
[ "$FP6_FINGERPRINT_QSEE_SIGNED_CONTROL_APP" = smplap64 ]
[ "$FP6_FINGERPRINT_QSEE_SIGNED_CONTROL_LOAD_ERRNO" = 5 ]
[ "$FP6_FINGERPRINT_QSEE_SIGNED_CONTROL_APP_COMMANDS" = 0 ]
[ "$FP6_FINGERPRINT_QSEE_SIGNED_CONTROL_CLEANUP_ACCEPTED" = true ]

stock_names_composer=$repo_root/scripts/mobile/prepare-fp6-fingerprint-stock-node-names-candidate.sh
stock_names_verifier=$repo_root/scripts/mobile/verify-fp6-fingerprint-stock-node-names-candidate.sh
[ -x "$stock_names_composer" ]
[ -x "$stock_names_verifier" ]
[ "$(sha256_file "$stock_names_composer")" = "$FP6_FINGERPRINT_STOCK_NODE_NAMES_COMPOSER_SHA256" ]
[ "$(sha256_file "$stock_names_verifier")" = "$FP6_FINGERPRINT_STOCK_NODE_NAMES_VERIFIER_SHA256" ]
grep -Fq 'qseecom_region' "$stock_names_composer"
grep -Fq 'qseecom_ta_region' "$stock_names_composer"
grep -Fq 'NORMALIZED_DTS_DIFF_LIMITED_TO_NODE_NAMES=true' "$stock_names_composer"
grep -Fq 'cmp "$work/base.dts" "$work/reverted-normalized.dts"' "$stock_names_composer"
! grep -Eq '(fastboot|ssh |/dev/focaltech_fp|qsee-app-loader)' "$stock_names_composer"

identity_v13_runner=$repo_root/scripts/mobile/run-fp6-fingerprint-identity-v13-remote.sh
signed_control_runner=$repo_root/scripts/mobile/run-fp6-qsee-signed-control-remote.sh
[ -x "$identity_v13_runner" ]
[ -x "$signed_control_runner" ]
[ "$(sha256_file "$identity_v13_runner")" = "$FP6_FINGERPRINT_STOCK_NODE_NAMES_IDENTITY_RUNNER_SHA256" ]
[ "$(sha256_file "$signed_control_runner")" = "$FP6_FINGERPRINT_QSEE_SIGNED_CONTROL_RUNNER_SHA256" ]
grep -Fq 'biometric_commands=0' "$repo_root/scripts/mobile/run-fp6-fingerprint-identity-v9-remote.sh"
grep -Fq -- "-e 's/focal64/smplap64/g'" "$signed_control_runner"

matched_cma_builder=$repo_root/scripts/mobile/build-fp6-fingerprint-matched-cma.sh
matched_cma_composer=$repo_root/scripts/mobile/prepare-fp6-fingerprint-matched-cma-candidate.sh
matched_cma_verifier=$repo_root/scripts/mobile/verify-fp6-fingerprint-matched-cma-candidate.sh
[ -x "$matched_cma_builder" ]
[ -x "$matched_cma_composer" ]
[ -x "$matched_cma_verifier" ]
[ "$(sha256_file "$matched_cma_builder")" = "$FP6_FINGERPRINT_MATCHED_CMA_BUILDER_SHA256" ]
[ "$(sha256_file "$matched_cma_composer")" = "$FP6_FINGERPRINT_MATCHED_CMA_COMPOSER_SHA256" ]
[ "$(sha256_file "$matched_cma_verifier")" = "$FP6_FINGERPRINT_MATCHED_CMA_VERIFIER_SHA256" ]
grep -Fq 'scripts/config --enable DMA_CMA' "$matched_cma_builder"
grep -Fq 'vmlinuz.efi modules' "$matched_cma_builder"
grep -Fq 'modules_install' "$matched_cma_builder"
grep -Fq 'COMPLETE_MATCHING_MODULE_TREE=true' "$matched_cma_builder"
grep -Fq '7.1.2-luma-fp-cma1' "$matched_cma_builder"
grep -Fq 'fdtput -d "$matched_dtb" "$apps" no-map' "$matched_cma_composer"
grep -Fq 'fdtput "$matched_dtb" "$apps" reusable' "$matched_cma_composer"
grep -Fq 'MATCHED_INITRAMFS_MODULES=true' "$matched_cma_composer"
! grep -Eq '(fastboot|ssh |/dev/focaltech_fp|qsee-app-loader)' "$matched_cma_composer"
grep -Fq 'FP6_FINGERPRINT_MATCHED_CMA_BOOT_SHA256' "$matched_cma_verifier"
grep -Fq 'old ABI modules remain in candidate initramfs' "$matched_cma_verifier"
! grep -Eq '(fastboot|ssh |/dev/focaltech_fp|qsee-app-loader)' "$matched_cma_verifier"

bridge_dir=$repo_root/src/fp6-fingerprint-qsee-bridge
[ -f "$bridge_dir/qseecom_bridge.c" ]
[ -f "$bridge_dir/qseecom_bridge.map" ]
[ -f "$bridge_dir/test_qseecom_bridge.c" ]
bridge_digest=$(
  cd "$bridge_dir"
  while IFS= read -r file; do
    printf '%s  %s\n' "$(sha256_file "$file")" "$file"
  done < <(find . -maxdepth 1 -type f \
    ! -name 'libQSEEComAPI*.so' ! -name 'test-qseecom-bridge' | LC_ALL=C sort) |
    sha256_stdin
)
[ "$bridge_digest" = "$FP6_FINGERPRINT_QSEECOM_BRIDGE_SOURCE_SHA256" ]
[ "$(grep -Ec '^[[:space:]]*QSEECom_[A-Za-z0-9_]+;' "$bridge_dir/qseecom_bridge.map")" -eq 3 ]
grep -Fq 'QSEECom_start_app;' "$bridge_dir/qseecom_bridge.map"
grep -Fq 'QSEECom_shutdown_app;' "$bridge_dir/qseecom_bridge.map"
grep -Fq 'QSEECom_send_cmd;' "$bridge_dir/qseecom_bridge.map"
! grep -Fq 'QSEECom_send_modified_cmd' "$bridge_dir/qseecom_bridge.map"

if git -C "$repo_root" ls-files | grep -Eq '(^|/)(focaltech_fp\.ko|libfingerprint\.default\.so|ff_ta-32|focal64\.(mdt|b[0-9][0-9]))$'; then
  printf 'FAIL: proprietary/stock fingerprint binary entered tracked source\n' >&2
  exit 1
fi

printf 'Mobile FP6 fingerprint identity and security gate: PASS\n'
