// SPDX-License-Identifier: Apache-2.0

/*
 * Android installd normally labels newly-created application data through
 * libselinux.  The FP6 Luma prototype host deliberately boots without an
 * active SELinux filesystem, so no backing filesystem can store those labels
 * and stock installd rejects otherwise valid private container directories.
 *
 * This process-local hook skips only restorecon operations, and only while
 * /sys/fs/selinux/enforce is absent.  It is loaded into the isolated Android
 * container's installd process, never into Fedora services.  If SELinux is
 * active it fails closed rather than inventing or translating a label.
 */

#include <sys/types.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int selinux_is_absent(void)
{
    return access("/sys/fs/selinux/enforce", F_OK) == -1;
}

__attribute__((visibility("default")))
int selinux_android_restorecon(const char *pathname, unsigned int flags)
{
    (void)pathname;
    (void)flags;
    return selinux_is_absent() ? 0 : -1;
}

__attribute__((visibility("default")))
int selinux_android_restorecon_pkgdir(const char *pathname, const char *seinfo,
                                     uid_t uid, unsigned int flags)
{
    (void)pathname;
    (void)seinfo;
    (void)uid;
    (void)flags;
    return selinux_is_absent() ? 0 : -1;
}

__attribute__((visibility("default")))
int lgetfilecon(const char *pathname, char **context)
{
    static const char unlabeled[] = "u:object_r:unlabeled:s0";
    (void)pathname;
    if (!selinux_is_absent() || context == NULL) {
        errno = ENOTSUP;
        return -1;
    }
    *context = malloc(sizeof(unlabeled));
    if (*context == NULL) {
        errno = ENOMEM;
        return -1;
    }
    memcpy(*context, unlabeled, sizeof(unlabeled));
    return (int)(sizeof(unlabeled) - 1);
}
