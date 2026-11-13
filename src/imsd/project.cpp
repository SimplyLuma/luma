// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file no-char-pointer
import std;
import Crafter.Build;
#pragma crafter dependency "https://forgejo.catcrafts.net/Catcrafts/Crafter.LintRules.git" commit "e906f797da2cd0b1776dd1b420f9b04d3cd24baf"
import Crafter.LintRules;
namespace fs = std::filesystem;
using namespace Crafter;

// pkg-config wrapper (std-only: no popen in import std). Returns the flags
// split on whitespace, or empty if pkg-config is unavailable — callers
// provide a fallback for that case.
static std::vector<std::string> PkgConfig(std::string_view args) {
    fs::path tmp = fs::temp_directory_path() /
        std::format("imsd-pkgconfig-{}.txt", std::hash<std::string_view>{}(args));
    std::string cmd = std::format("pkg-config {} > {} 2>/dev/null", args, tmp.string());
    std::vector<std::string> flags;
    if (std::system(cmd.c_str()) == 0) {
        std::ifstream f(tmp);
        std::string flag;
        while (f >> flag)
            flags.push_back(flag);
    }
    std::error_code ec;
    fs::remove(tmp, ec);
    return flags;
}

// GDBus (gio-2.0) — bus layer for the two daemons; chosen over sd-bus because
// the platform stack (ModemManager, libqmi for the future direct-AKA port) is
// GLib, so they want a GMainLoop regardless.
static void ApplyGioFlags(Configuration& cfg) {
    if (!cfg.sysroot.empty()) {
        // cross build (e.g. --target=aarch64-alpine-linux-musl
        // --sysroot=<packaging/make-sysroot.sh output>): the host's
        // pkg-config would answer for the wrong architecture. glib's
        // include layout is stable across distros; the `-I=` form makes
        // clang resolve the paths inside the sysroot.
        cfg.compileFlags.push_back("-I=/usr/include/glib-2.0");
        cfg.compileFlags.push_back("-I=/usr/lib/glib-2.0/include");
        for (const char* l : { "-lgio-2.0", "-lgobject-2.0", "-lglib-2.0" })
            cfg.linkFlags.push_back(l);
    } else {
        // native build: ask pkg-config. Only -I/-D flags pass through:
        // pkg-config also emits -pthread, which would config-mismatch the
        // shared prebuilt std.pcm (and pthread lives in libc on every
        // platform we target).
        for (std::string& f : PkgConfig("--cflags gio-2.0"))
            if (f.starts_with("-I") || f.starts_with("-D")) cfg.compileFlags.push_back(std::move(f));
        std::vector<std::string> libs;
        for (std::string& f : PkgConfig("--libs gio-2.0"))
            if (f.starts_with("-l") || f.starts_with("-L")) libs.push_back(std::move(f));
        if (libs.empty()) libs = { "-lgio-2.0", "-lgobject-2.0", "-lglib-2.0" };
        for (std::string& f : libs)
            cfg.linkFlags.push_back(std::move(f));
    }
}

// ImsdBus — the polkit gate imsd and imsd-ofonod put in front of call
// control. A static library of its own: GLib, so outside imsd-core.
static std::unique_ptr<Configuration> BusLibrary(std::span<const std::string_view> args) {
    auto bus = std::make_unique<Configuration>();
    bus->path = "./";
    bus->name = "imsd-bus";
    bus->outputName = "imsd-bus";
    ApplyStandardArgs(*bus, args);
    bus->type = ConfigurationType::LibraryStatic;
    {
        std::array<fs::path, 1> ifaces = { "interfaces/ImsdBus" };
        std::array<fs::path, 0> impls = {};
        bus->GetInterfacesAndImplementations(ifaces, impls);
    }
    ApplyGioFlags(*bus);
    return bus;
}

extern "C" Configuration CrafterBuildProject(std::span<const std::string_view> args) {
    // Five executables come out of this repo; which one a build produces is
    // chosen by `--product=`:
    //   daemon (default) — imsd, the control-plane daemon (GDBus + engine),
    //                      and the unit tests, which link imsd-core.
    //   media            — imsd-media, the standalone RTP/AMR-WB data plane
    //                      the daemon spawns per call (pure std + POSIX, no
    //                      core, no GLib). Built with `-- --product=media`.
    //   dialerd          — imsd-dialerd, the session-bus Plasma Dialer
    //                      backend (org.kde.telephony.*) bridging to imsd
    //                      (pure GDBus translation, no core).
    //   ofonod           — imsd-ofonod, the system-bus GNOME Calls backend
    //                      (org.ofono.*) bridging to imsd, for Phosh and
    //                      GNOME Mobile (GDBus translation plus the imsd-bus
    //                      polkit gate on its callers, no core).
    //   pcscf            — imsd-pcscf, the P-CSCF reader ims-pdn-up.sh runs
    //                      once the ims PDN is up: one QMI WDS client over a
    //                      raw QRTR socket (pure std + POSIX, links the core
    //                      for Imsd:Qmi, no GLib).
    bool wantMedia = false;
    bool wantDialerd = false;
    bool wantOfonod = false;
    bool wantPcscf = false;
    for (std::string_view a : args) {
        if (a == "--product=media") wantMedia = true;
        if (a == "--product=dialerd") wantDialerd = true;
        if (a == "--product=ofonod") wantOfonod = true;
        if (a == "--product=pcscf") wantPcscf = true;
    }
    if (wantOfonod) {
        Configuration ofonod;
        ofonod.path = "./";
        ofonod.name = "imsd-ofonod";
        ofonod.outputName = "imsd-ofonod";
        ApplyStandardArgs(ofonod, args);
        ofonod.type = ConfigurationType::Executable;
        static auto Bus = BusLibrary(args);
        ofonod.dependencies = { Bus.get() };
        {
            std::array<fs::path, 0> ifaces = {};
            std::array<fs::path, 1> impls = { "implementations/ofonod" };
            ofonod.GetInterfacesAndImplementations(ifaces, impls);
        }
        ApplyGioFlags(ofonod);
        LintRules::AddRules(ofonod);
        return ofonod;
    }
    if (wantDialerd) {
        Configuration dialerd;
        dialerd.path = "./";
        dialerd.name = "imsd-dialerd";
        dialerd.outputName = "imsd-dialerd";
        ApplyStandardArgs(dialerd, args);
        dialerd.type = ConfigurationType::Executable;
        {
            std::array<fs::path, 0> ifaces = {};
            std::array<fs::path, 1> impls = { "implementations/dialerd" };
            dialerd.GetInterfacesAndImplementations(ifaces, impls);
        }
        ApplyGioFlags(dialerd);
        LintRules::AddRules(dialerd);
        return dialerd;
    }
    if (wantMedia) {
        Configuration media;
        media.path = "./";
        media.name = "imsd-media";
        media.outputName = "imsd-media";
        ApplyStandardArgs(media, args);
        media.type = ConfigurationType::Executable;
        {
            std::array<fs::path, 0> ifaces = {};
            std::array<fs::path, 1> impls = { "implementations/media" };
            media.GetInterfacesAndImplementations(ifaces, impls);
        }
        media.linkFlags.push_back("-ldl");       // dlopen the AMR-WB codecs
        media.linkFlags.push_back("-lpthread");  // std::jthread playout/mic
        LintRules::AddRules(media);
        return media;
    }

    // imsd-core — the engine as a static library of pure C++ modules (SIP,
    // SDP, later AKA/IPsec/media/engine). Deliberately GLib-free so every
    // piece is unit-testable without a bus or a phone.
    static auto Core = std::make_unique<Configuration>();
    Core->path = "./";
    Core->name = "imsd-core";
    Core->outputName = "imsd-core";
    ApplyStandardArgs(*Core, args);
    Core->type = ConfigurationType::LibraryStatic;
    {
        std::array<fs::path, 9> ifaces = {
            "interfaces/Imsd",
            "interfaces/Imsd-Util",
            "interfaces/Imsd-Aka",
            "interfaces/Imsd-Ipsec",
            "interfaces/Imsd-Qmi",
            "interfaces/Imsd-Sip",
            "interfaces/Imsd-Sdp",
            "interfaces/Imsd-Messages",
            "interfaces/Imsd-Engine",
        };
        std::array<fs::path, 0> impls = {};
        Core->GetInterfacesAndImplementations(ifaces, impls);
    }

    if (wantPcscf) {
        Configuration pcscf;
        pcscf.path = "./";
        pcscf.name = "imsd-pcscf";
        pcscf.outputName = "imsd-pcscf";
        ApplyStandardArgs(pcscf, args);
        pcscf.type = ConfigurationType::Executable;
        pcscf.dependencies = { Core.get() };
        {
            std::array<fs::path, 0> ifaces = {};
            std::array<fs::path, 1> impls = { "implementations/pcscf" };
            pcscf.GetInterfacesAndImplementations(ifaces, impls);
        }
        LintRules::AddRules(pcscf);
        return pcscf;
    }

    // imsd — the daemon executable: GLib main loop + GDBus service
    // (net.catcrafts.IMS1, same ABI as the Python imsd.py) around the core.
    Configuration cfg;
    cfg.path = "./";
    cfg.name = "imsd";
    cfg.outputName = "imsd";
    ApplyStandardArgs(cfg, args);
    cfg.type = ConfigurationType::Executable;
    static auto Bus = BusLibrary(args);
    cfg.dependencies = { Core.get(), Bus.get() };
    {
        std::array<fs::path, 0> ifaces = {};
        std::array<fs::path, 1> impls = { "implementations/main" };
        cfg.GetInterfacesAndImplementations(ifaces, impls);
    }

    ApplyGioFlags(cfg);
    cfg.linkFlags.push_back("-lpthread");  // std::thread engine loop

    cfg.AddTest("Util").Dependencies({ Core.get() });
    cfg.AddTest("Aka").Dependencies({ Core.get() });
    cfg.AddTest("Ipsec").Dependencies({ Core.get() });
    cfg.AddTest("Qmi").Dependencies({ Core.get() });
    cfg.AddTest("Messages").Dependencies({ Core.get() });
    cfg.AddTest("Engine").Dependencies({ Core.get() });
    cfg.AddTest("Sip").Dependencies({ Core.get() });
    cfg.AddTest("Sdp").Dependencies({ Core.get() });

    LintRules::AddRules(cfg);

    return cfg;
}
