/*
 * Project Luma FP6 isolated Android netd BPF loader.
 *
 * This deliberately does not reproduce Android netbpfload's boot-policy
 * preflight.  The QREL 16.95.0 netbpfload rejects Linux 7.1 because that
 * version is not in Android 16's LTS allowlist and then attempts to change a
 * host-global sysctl.  C1 instead gives this process a private bpffs and an
 * isolated network namespace.  The exact stock libbpf_android.so still parses,
 * verifies, loads, and pins the exact stock netd.o.
 */

#include <linux/bpf.h>
#include <sys/system_properties.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstring>

namespace android::bpf {

// Exact public ABI from AOSP system/bpf android16-release at
// 4447acd742bf443f9088c300bd69f96ede8eaeb1.
struct Location {
    const char* const dir = "";
    const char* const prefix = "";
    const bpf_prog_type* allowedProgTypes = nullptr;
    size_t allowedProgTypesLength = 0;
};

int loadProg(const char* elf_path, bool* is_critical,
             const Location& location = {});
int createSysFsBpfSubDir(const char* prefix);

}  // namespace android::bpf

namespace {

constexpr char kNetdObject[] =
        "/apex/com.android.tethering/etc/bpf/netd_shared/netd.o";
constexpr char kPinPrefix[] = "netd_shared/";
constexpr char kRequiredPin[] =
        "/sys/fs/bpf/netd_shared/prog_netd_skfilter_allowlist_xtbpf";

int fail(const char* step, int result) {
    std::fprintf(stderr, "luma-netbpfload: %s failed: result=%d errno=%d (%s)\n",
                 step, result, errno, std::strerror(errno));
    return 1;
}

}  // namespace

int main() {
    const android::bpf::Location location = {
            .dir = "/apex/com.android.tethering/etc/bpf/netd_shared/",
            .prefix = kPinPrefix,
            .allowedProgTypes = nullptr,
            .allowedProgTypesLength = 0,
    };

    int result = android::bpf::createSysFsBpfSubDir(kPinPrefix);
    if (result != 0) return fail("create private netd_shared bpffs directory", result);

    bool critical = false;
    result = android::bpf::loadProg(kNetdObject, &critical, location);
    if (result != 0) return fail("load exact stock netd.o", result);
    if (!critical) return fail("validate netd.o critical marker", -EINVAL);
    if (access(kRequiredPin, F_OK) != 0) return fail("validate required netd pin", -ENOENT);

    // Publish Android's normal completion property only after a real load and
    // required-pin check.  This is completion signaling, not a property shim.
    result = __system_property_set("bpf.progs_loaded", "1");
    if (result != 0) return fail("publish bpf.progs_loaded", result);

    std::fprintf(stderr,
                 "luma-netbpfload: loaded exact stock netd.o into private bpffs\n");
    return 0;
}
