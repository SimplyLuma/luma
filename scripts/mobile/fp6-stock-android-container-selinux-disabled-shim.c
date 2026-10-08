// SPDX-License-Identifier: Apache-2.0

/*
 * Android zygote requires selinux_android_setcon() while forking
 * system_server.  The FP6 Luma host currently boots with SELinux disabled, so
 * the Android label cannot be installed and stock zygote treats ENOTSUP as
 * fatal.  This process-local compatibility hook succeeds only when the host
 * exposes no active SELinux filesystem.  It deliberately fails closed if
 * SELinux is enabled; it neither invents nor translates security labels.
 */

#include <unistd.h>

__attribute__((visibility("default")))
int selinux_android_setcon(const char *context)
{
    (void)context;
    return access("/sys/fs/selinux/enforce", F_OK) == -1 ? 0 : -1;
}
