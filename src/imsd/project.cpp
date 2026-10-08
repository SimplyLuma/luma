// SPDX-License-Identifier: GPL-3.0-only
// SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®

// lint-disable-file no-char-pointer
import std;
import Crafter.Build;
#include "lint-rules.h"
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

extern "C" Configuration CrafterBuildProject(std::span<const std::string_view> args) {
    // Three executables come out of this repo; which one a build produces is
    // chosen by `--product=`:
    //   daemon (default) — imsd, the control-plane daemon (GDBus + engine),
    //                      and the unit tests, which link imsd-core.
    //   media            — imsd-media, the standalone RTP/AMR-WB data plane
    //                      the daemon spawns per call (pure std + POSIX, no
    //                      core, no GLib). Built with `-- --product=media`.
    //   dialerd          — imsd-dialerd, the session-bus Plasma Dialer
    //                      backend (org.kde.telephony.*) bridging to imsd
    //                      (pure GDBus translation, no core).
    bool wantMedia = false;
    bool wantDialerd = false;
    for (std::string_view a : args) {
        if (a == "--product=media") wantMedia = true;
        if (a == "--product=dialerd") wantDialerd = true;
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
        ProjectLint::AddProjectLintRules(dialerd);
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
        ProjectLint::AddProjectLintRules(media);
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
        std::array<fs::path, 8> ifaces = {
            "interfaces/Imsd",
            "interfaces/Imsd-Util",
            "interfaces/Imsd-Aka",
            "interfaces/Imsd-Ipsec",
            "interfaces/Imsd-Sip",
            "interfaces/Imsd-Sdp",
            "interfaces/Imsd-Messages",
            "interfaces/Imsd-Engine",
        };
        std::array<fs::path, 0> impls = {};
        Core->GetInterfacesAndImplementations(ifaces, impls);
    }

    // imsd — the daemon executable: GLib main loop + GDBus service
    // (net.catcrafts.IMS1, same ABI as the Python imsd.py) around the core.
    Configuration cfg;
    cfg.path = "./";
    cfg.name = "imsd";
    cfg.outputName = "imsd";
    ApplyStandardArgs(cfg, args);
    cfg.type = ConfigurationType::Executable;
    cfg.dependencies = { Core.get() };
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
    cfg.AddTest("Messages").Dependencies({ Core.get() });
    cfg.AddTest("Engine").Dependencies({ Core.get() });
    cfg.AddTest("Sip").Dependencies({ Core.get() });
    cfg.AddTest("Sdp").Dependencies({ Core.get() });

    ProjectLint::AddProjectLintRules(cfg);

    return cfg;
}
