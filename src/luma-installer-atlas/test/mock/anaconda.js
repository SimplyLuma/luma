/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * TEST FIXTURE. An in-memory stand-in for Anaconda's D-Bus modules, shaped
 * like the replies anaconda-core 44.30 gives, so Atlas can be rendered and
 * walked in a browser without an installer. It is bundled only into
 * dist-preview/ by `ATLAS_PREVIEW=1 node build.js` and is never packaged.
 * Every value below is invented for the screenshots and matches the design
 * handoff's example machine; none of it reaches the product.
 */

const V = (t, v) => ({ t, v });
const GiB = 1024 ** 3;
const MiB = 1024 ** 2;

const S = "org.fedoraproject.Anaconda.Modules.Storage";

const disk = ({ bus, children = [], description, id, model, removable = false, size, vendor = "" }) => ({
    attrs: V("a{ss}", { bus, model, vendor }),
    children: V("as", children),
    description: V("s", description),
    "device-id": V("s", id),
    "is-disk": V("b", true),
    links: V("as", []),
    name: V("s", id),
    parents: V("as", []),
    path: V("s", `/dev/${id}`),
    protected: V("b", false),
    removable: V("b", removable),
    size: V("t", size),
    type: V("s", "disk"),
});

const child = ({ id, name, parents, size, type }) => ({
    attrs: V("a{ss}", {}),
    children: V("as", []),
    description: V("s", ""),
    "device-id": V("s", id),
    "is-disk": V("b", false),
    links: V("as", []),
    name: V("s", name || id),
    parents: V("as", parents),
    path: V("s", `/dev/${name || id}`),
    protected: V("b", false),
    removable: V("b", false),
    size: V("t", size),
    type: V("s", type),
});

const format = (type, { attrs = {}, description = type, mountable = false } = {}) => ({
    attrs: V("a{ss}", attrs),
    description: V("s", description),
    formattable: V("b", true),
    mountable: V("b", mountable),
    type: V("s", type),
});

const withChildren = (devices) => {
    Object.values(devices).forEach(device => {
        device.children = V("as", Object.keys(devices).filter(id => devices[id].parents.v.includes(device["device-id"].v)));
    });
    return devices;
};

const originalTree = () => withChildren({
    nvme0n1: { ...disk({ bus: "nvme", description: "Samsung SSD 990 PRO 1TB", id: "nvme0n1", model: "Samsung 990 PRO", size: 1000204886016 }), format: format("") },
    sda: { ...disk({ bus: "ata", description: "CT500MX500SSD1", id: "sda", model: "Crucial MX500", size: 500107862016 }), format: format("disklabel") },
    sda1: { ...child({ id: "sda1", parents: ["sda"], size: 100 * MiB, type: "partition" }), format: format("efi", { mountable: true }) },
    sda2: { ...child({ id: "sda2", parents: ["sda"], size: 16 * MiB, type: "partition" }), format: format("") },
    sda3: { ...child({ id: "sda3", parents: ["sda"], size: 499.3e9, type: "partition" }), format: format("ntfs", { mountable: true }) },
    sdb: { ...disk({ bus: "usb", description: "SanDisk Ultra", id: "sdb", model: "Ultra", removable: true, size: 61530439680, vendor: "SanDisk" }), format: format("iso9660"), protected: V("b", true) },
});

const plannedEraseAll = (encrypted) => {
    const tree = originalTree();
    delete tree.nvme0n1;
    const base = "nvme0n1";
    const devices = {
        ...tree,
        [base]: { ...disk({ bus: "nvme", description: "Samsung SSD 990 PRO 1TB", id: base, model: "Samsung 990 PRO", size: 1000204886016 }), format: format("disklabel") },
        nvme0n1p1: { ...child({ id: "nvme0n1p1", parents: [base], size: 600 * MiB, type: "partition" }), format: format("efi", { attrs: { "mount-point": "/boot/efi" }, mountable: true }) },
        nvme0n1p2: { ...child({ id: "nvme0n1p2", parents: [base], size: 2 * GiB, type: "partition" }), format: format("ext4", { attrs: { "mount-point": "/boot" }, mountable: true }) },
        nvme0n1p3: { ...child({ id: "nvme0n1p3", parents: [base], size: 999.5 * 1000 ** 3 - 2.6 * GiB, type: "partition" }), format: format(encrypted ? "luks" : "btrfs") },
    };
    const volumeParent = encrypted ? "luks-nvme0n1p3" : "nvme0n1p3";
    if (encrypted) {
        devices["luks-nvme0n1p3"] = { ...child({ id: "luks-nvme0n1p3", parents: ["nvme0n1p3"], size: 996.7e9, type: "luks/dm-crypt" }), format: format("btrfs") };
    }
    devices.btrfs = { ...child({ id: "btrfs", name: "luma", parents: [volumeParent], size: 996.7e9, type: "btrfs volume" }), format: format("btrfs", { mountable: true }) };
    // Anaconda 44.30's real plan for this payload: subvolumes root and home.
    ["root", "home"].forEach(name => {
        devices[name] = { ...child({ id: name, name, parents: ["btrfs"], size: 996.7e9, type: "btrfs subvolume" }), format: format("btrfs", { mountable: true }) };
    });
    return {
        actions: [],
        devices: withChildren(devices),
        mountPoints: { "/": "root", "/boot": "nvme0n1p2", "/boot/efi": "nvme0n1p1", "/home": "home" },
    };
};

const LOCALES = [
    ["en", "en_US.UTF-8", "English (United States)", "English (United States)"],
    ["en", "en_GB.UTF-8", "English (United Kingdom)", "English (United Kingdom)"],
    ["es", "es_ES.UTF-8", "Español (España)", "Spanish (Spain)"],
    ["es", "es_MX.UTF-8", "Español (México)", "Spanish (Mexico)"],
    ["fr", "fr_FR.UTF-8", "Français (France)", "French (France)"],
    ["de", "de_DE.UTF-8", "Deutsch (Deutschland)", "German (Germany)"],
    ["ja", "ja_JP.UTF-8", "日本語 (日本)", "Japanese (Japan)"],
    ["pt", "pt_BR.UTF-8", "Português (Brasil)", "Portuguese (Brazil)"],
    ["it", "it_IT.UTF-8", "Italiano (Italia)", "Italian (Italy)"],
    ["zh", "zh_CN.UTF-8", "中文 (中国)", "Chinese (China)"],
    ["nl", "nl_NL.UTF-8", "Nederlands (Nederland)", "Dutch (Netherlands)"],
    ["pl", "pl_PL.UTF-8", "Polski (Polska)", "Polish (Poland)"],
];

const LAYOUTS = [
    ["us", "English (US)", true], ["gb", "English (UK)", true], ["es", "Spanish", true],
    ["fr", "French (AZERTY)", true], ["de", "German (QWERTZ)", true], ["jp", "Japanese", false],
];

const ZONES = {
    Africa: ["Cairo", "Johannesburg", "Lagos", "Nairobi"],
    America: ["Chicago", "Denver", "Los_Angeles", "Mexico_City", "New_York", "Sao_Paulo", "Toronto", "Argentina/Buenos_Aires"],
    Asia: ["Kolkata", "Seoul", "Shanghai", "Singapore", "Tokyo"],
    Australia: ["Melbourne", "Perth", "Sydney"],
    Europe: ["Berlin", "London", "Madrid", "Paris", "Rome"],
    Pacific: ["Auckland", "Honolulu"],
};

export const createAnaconda = (options) => {
    const listeners = [];
    const state = {
        applied: "",
        encrypted: false,
        language: "en_US.UTF-8",
        ntp: true,
        systemDateTime: new Date().toISOString(),
        partitionings: {},
        passphrase: "",
        partitioningCount: 0,
        selectedDisks: [],
        timezone: "America/Los_Angeles",
        users: [],
        xlayouts: ["us"],
    };

    const emit = (path, iface, signal, args) => setTimeout(() => listeners.forEach(fn => fn(path, iface, signal, args)), 0);

    const tasks = {};
    let taskCount = 0;
    const makeTask = ({ result, run }) => {
        const path = `/org/fedoraproject/Anaconda/Task/${++taskCount}`;
        tasks[path] = { listeners: {}, result, run };
        return path;
    };
    if (options.resumeFailure) {
        makeTask({ run: "install" });
    }

    const devicesFor = (deviceTreePath) => {
        const planned = state.applied && deviceTreePath !== "original";
        const tree = planned ? plannedEraseAll(state.partitionings[state.applied]?.encrypted) : { actions: [], devices: originalTree(), mountPoints: {} };
        // Like Anaconda, a reset (or applied) storage model only holds the
        // selected disks and protected devices.
        if (state.limited && state.selectedDisks.length) {
            const keep = (id) => {
                let current = id;
                while (tree.devices[current]?.parents.v.length) {
                    current = tree.devices[current].parents.v[0];
                }
                return state.selectedDisks.includes(current) || tree.devices[current]?.protected.v;
            };
            tree.devices = Object.fromEntries(Object.entries(tree.devices).filter(([id]) => keep(id)));
        }
        return tree;
    };

    const existingSystemsAll = [{
        devices: V("as", ["sda1", "sda3"]),
        "mount-points": V("a{ss}", {}),
        "os-name": V("s", "Windows 11"),
    }];

    const viewer = (method, args, planned) => {
        const { actions, devices, mountPoints } = devicesFor(planned ? "planned" : "original");
        switch (method) {
        case "GetDevices": return [Object.keys(devices)];
        case "GetActions": return [actions];
        case "GetMountPoints": return [mountPoints];
        case "GetExistingSystems": return [existingSystemsAll.filter(system => system.devices.v.every(id => devices[id]))];
        case "GetDeviceData": {
            const device = devices[args[0]];
            if (!device) {
                throw Object.assign(new Error("unknown device"), { name: `${S}.UnknownDeviceError` });
            }
            const { format: _format, ...data } = device;
            return [data];
        }
        case "GetFormatData": return [devices[args[0]]?.format || format("")];
        case "GetDiskTotalSpace": return [args[0].reduce((sum, id) => sum + (devices[id]?.size.v || 0), 0)];
        case "GetDiskFreeSpace": return [args[0].reduce((sum, id) => sum + ({ nvme0n1: devices[id]?.size.v, sda: 190e9, sdb: 0 }[id] ?? 0), 0)];
        case "GetRequiredDeviceSize": return [Math.round(args[0] * 1.35)];
        case "GetFreeSpaceForSystem": return [planned ? 996.7e9 : 0];
        case "GetMountPointConstraints": return [[
            { "mount-point": V("s", "/"), recommended: V("b", false), required: V("b", true), "required-filesystem-type": V("s", "") },
            { "mount-point": V("s", "/boot/efi"), recommended: V("b", false), required: V("b", true), "required-filesystem-type": V("s", "efi") },
            { "mount-point": V("s", "/boot"), recommended: V("b", true), required: V("b", false), "required-filesystem-type": V("s", "") },
        ]];
        case "GetFormatTypeData": return [{ description: V("s", "EFI System Partition") }];
        default: throw new Error(`mock: DeviceTree.${method} not implemented`);
        }
    };

    const properties = {
        [`${S}`]: {
            AppliedPartitioning: () => V("s", state.applied),
            CreatedPartitioning: () => V("ao", Object.keys(state.partitionings)),
        },
        [`${S}.DiskSelection`]: {
            IgnoredDisks: () => V("as", []),
            SelectedDisks: () => V("as", state.selectedDisks),
        },
        "org.fedoraproject.Anaconda.Modules.Localization": {
            Language: () => V("s", state.language),
            XLayouts: () => V("as", state.xlayouts),
        },
        "org.fedoraproject.Anaconda.Modules.Network": {
            Connected: () => V("b", options.offline !== true),
            Hostname: () => V("s", ""),
        },
        "org.fedoraproject.Anaconda.Modules.Payloads": {
            ActivePayload: () => V("o", "/org/fedoraproject/Anaconda/Modules/Payloads/Payload/1"),
        },
        "org.fedoraproject.Anaconda.Modules.Payloads.Payload": {
            Sources: () => V("ao", ["/org/fedoraproject/Anaconda/Modules/Payloads/Source/1"]),
            Type: () => V("s", "RPM_OSTREE"),
        },
        "org.fedoraproject.Anaconda.Modules.Payloads.Source": {
            Type: () => V("s", "RPM_OSTREE"),
        },
        "org.fedoraproject.Anaconda.Modules.Payloads.Source.RPMOSTree": {
            Configuration: () => V("a{sv}", {
                osname: V("s", "luma"), ref: V("s", "luma/1/x86_64/stable"), remote: V("s", "luma"), url: V("s", "file:///run/install/repo/luma/repo"),
            }),
        },
        "org.fedoraproject.Anaconda.Modules.Runtime.UserInterface": {
            PasswordPolicies: () => V("a{sa{sv}}", {
                luks: { "is-strict": V("b", false), "min-length": V("u", 8), "min-quality": V("u", 1) },
                root: { "is-strict": V("b", false), "min-length": V("u", 6), "min-quality": V("u", 1) },
                user: { "is-strict": V("b", false), "min-length": V("u", 6), "min-quality": V("u", 1) },
            }),
        },
        "org.fedoraproject.Anaconda.Modules.Timezone": {
            NTPEnabled: () => V("b", state.ntp),
            Timezone: () => V("s", state.timezone),
        },
        "org.fedoraproject.Anaconda.Modules.Users": {
            Users: () => V("aa{sv}", state.users),
        },
        "org.fedoraproject.Anaconda.Task": {
            Steps: () => V("i", 40),
            IsRunning: () => V("b", false),
            Progress: () => V("(is)", [9, "Receiving objects: 35%"]),
        },
        [`${S}.Partitioning`]: {
            PartitioningMethod: (path) => V("s", state.partitionings[path]?.method || "AUTOMATIC"),
        },
        [`${S}.Partitioning.Automatic`]: {
            Request: (path) => V("a{sv}", { encrypted: V("b", !!state.partitionings[path]?.encrypted), passphrase: V("s", state.partitionings[path]?.passphrase || "") }),
        },
    };

    const call = async (path, iface, method, args = []) => {
        await new Promise(resolve => setTimeout(resolve, options.latency ?? 5));

        if (iface === "org.freedesktop.DBus.Properties") {
            const [propIface, name, value] = args;
            if (method === "Get") {
                const getter = properties[propIface]?.[name];
                if (!getter) {
                    throw new Error(`mock: property ${propIface}.${name} not implemented`);
                }
                return [getter(path)];
            }
            if (method === "GetAll") {
                const all = {};
                Object.entries(properties[propIface] || {}).forEach(([key, getter]) => { all[key] = getter(path) });
                return [all];
            }
            if (method === "Set") {
                if (propIface === `${S}.DiskSelection` && name === "SelectedDisks") {
                    state.selectedDisks = value.v;
                    emit(path, iface, "PropertiesChanged", [`${S}.DiskSelection`, { SelectedDisks: value }, []]);
                } else if (propIface === "org.fedoraproject.Anaconda.Modules.Localization" && name === "Language") {
                    state.language = value.v;
                    emit(path, iface, "PropertiesChanged", [propIface, { Language: value }, []]);
                } else if (propIface === "org.fedoraproject.Anaconda.Modules.Localization" && name === "XLayouts") {
                    state.xlayouts = value.v.length ? value.v : state.xlayouts;
                } else if (propIface === "org.fedoraproject.Anaconda.Modules.Timezone" && name === "NTPEnabled") {
                    state.ntp = value.v;
                } else if (propIface === `${S}.Partitioning.Automatic` && name === "Request") {
                    state.partitionings[path] = { ...state.partitionings[path], encrypted: value.v.encrypted?.v };
                    window.__atlasMockEncryptionRequests = [...(window.__atlasMockEncryptionRequests || []), value.v.encrypted?.v];
                } else if (propIface === "org.fedoraproject.Anaconda.Modules.Users" && name === "Users") {
                    state.users = value.v;
                }
                return [];
            }
        }

        if (path.startsWith("/org/fedoraproject/Anaconda/Task/")) {
            const task = tasks[path];
            if (iface === "org.fedoraproject.Anaconda.Task" && method === "GetResult") {
                return [task.result];
            }
        }

        switch (`${iface}.${method}`) {
        case "org.fedoraproject.Anaconda.Boss.SetLocale": return [];
        case "org.fedoraproject.Anaconda.Boss.InstallWithTasks":
            return [[makeTask({ install: true, run: "install" })]];
        case "org.fedoraproject.Anaconda.Modules.Localization.GetLanguages":
            return [[...new Set(LOCALES.map(locale => locale[0]))]];
        case "org.fedoraproject.Anaconda.Modules.Localization.GetLanguageData": {
            const locale = LOCALES.find(l => l[0] === args[0]);
            return [{ "english-name": V("s", locale[3].split(" (")[0]), "language-id": V("s", args[0]), "native-name": V("s", locale[2].split(" (")[0]) }];
        }
        case "org.fedoraproject.Anaconda.Modules.Localization.GetLocales":
            return [LOCALES.filter(l => l[0] === args[0]).map(l => l[1])];
        case "org.fedoraproject.Anaconda.Modules.Localization.GetLocaleData": {
            const locale = LOCALES.find(l => l[1] === args[0]);
            return [{ "english-name": V("s", locale[3]), "language-id": V("s", locale[0]), "locale-id": V("s", locale[1]), "native-name": V("s", locale[2]) }];
        }
        case "org.fedoraproject.Anaconda.Modules.Localization.GetCommonLocales":
            return [["de_DE.UTF-8", "en_GB.UTF-8", "en_US.UTF-8", "es_ES.UTF-8", "fr_FR.UTF-8", "it_IT.UTF-8", "ja_JP.UTF-8", "pt_BR.UTF-8", "zh_CN.UTF-8"]];
        case "org.fedoraproject.Anaconda.Modules.Localization.GetKeyboardLayouts":
            return [LAYOUTS.map(([id, description, common]) => ({ description: V("s", description), "is-common": V("b", common), "layout-id": V("s", id), "supports-ascii": V("b", id !== "jp") }))];
        case "org.fedoraproject.Anaconda.Modules.Localization.GetKeyboardConfigurationWithTask":
            return [makeTask({ result: V("(assas)", [state.xlayouts, state.xlayouts[0]]) })];
        case "org.fedoraproject.Anaconda.Modules.Localization.SetCompositorLayouts": return [];
        case "org.fedoraproject.Anaconda.Modules.Localization.SetXKeyboardDefaults": {
            const map = { de: "de", en: state.language.startsWith("en_GB") ? "gb" : "us", es: "es", fr: "fr", ja: "jp" };
            state.xlayouts = [map[state.language.slice(0, 2)] || "us"];
            return [];
        }
        case "org.fedoraproject.Anaconda.Modules.Payloads.CalculateRequiredSpace": return [18.5e9];
        case "org.fedoraproject.Anaconda.Modules.Timezone.GetAllValidTimezones": return [ZONES];
        case "org.fedoraproject.Anaconda.Modules.Timezone.SetTimezoneWithPriority":
            state.timezone = args[0];
            emit("/org/fedoraproject/Anaconda/Modules/Timezone", "org.freedesktop.DBus.Properties", "PropertiesChanged", ["org.fedoraproject.Anaconda.Modules.Timezone", { Timezone: V("s", args[0]) }, []]);
            return [];
        case "org.fedoraproject.Anaconda.Modules.Timezone.GetSystemDateTime": return [state.systemDateTime];
        case "org.fedoraproject.Anaconda.Modules.Timezone.SetSystemDateTime": state.systemDateTime = args[0]; return [];
        case "org.fedoraproject.Anaconda.Modules.Users.GuessUsernameFromFullName":
            return [(args[0] || "").trim().split(/\s+/)[0].toLowerCase().normalize("NFKD").replace(/[^a-z0-9_-]/g, "")];
        case "org.fedoraproject.Anaconda.Modules.Users.SetCryptedRootPassword":
        case "org.fedoraproject.Anaconda.Modules.Users.ClearRootPassword": return [];
        case `${S}.DiskSelection.GetUsableDisks`: return [["nvme0n1", "sda"]];
        case `${S}.CreatePartitioning`: {
            const partitioning = `/org/fedoraproject/Anaconda/Modules/Storage/Partitioning/${++state.partitioningCount}`;
            state.partitionings[partitioning] = { encrypted: false, method: args[0] };
            emit("/org/fedoraproject/Anaconda/Modules/Storage", "org.freedesktop.DBus.Properties", "PropertiesChanged", [S, { CreatedPartitioning: V("ao", Object.keys(state.partitionings)) }, []]);
            return [partitioning];
        }
        case `${S}.ResetPartitioning`:
            state.applied = "";
            state.limited = true;
            emit("/org/fedoraproject/Anaconda/Modules/Storage", "org.freedesktop.DBus.Properties", "PropertiesChanged", [S, { AppliedPartitioning: V("s", "") }, []]);
            return [];
        case `${S}.ApplyPartitioning`:
            state.applied = args[0];
            emit("/org/fedoraproject/Anaconda/Modules/Storage", "org.freedesktop.DBus.Properties", "PropertiesChanged", [S, { AppliedPartitioning: V("s", args[0]) }, []]);
            return [];
        case `${S}.ScanDevicesWithTask`: return [makeTask({})];
        case `${S}.Partitioning.GetDeviceTree`: return [`${path}/DeviceTree`];
        case `${S}.Partitioning.ConfigureWithTask`: return [[makeTask({})]];
        case `${S}.Partitioning.ValidateWithTask`: return [[makeTask({ result: V("a{sv}", { "error-messages": V("as", []), "warning-messages": V("as", []) }) })]];
        case `${S}.Partitioning.Automatic.SetPassphrase`:
            state.partitionings[path] = { ...state.partitionings[path], passphrase: args[0] };
            return [];
        case `${S}.Partitioning.Manual.GatherRequests`: return [[]];
        case `${S}.DiskInitialization.SetInitializationMode`: return [];
        default:
            break;
        }

        if (iface === `${S}.DeviceTree.Viewer`) {
            return viewer(method, args, !!state.applied);
        }

        throw new Error(`mock: ${iface}.${method} on ${path} not implemented`);
    };

    const runTask = (path) => {
        const task = tasks[path];
        const fire = (signal, ...args) => (task.listeners[signal] || []).forEach(fn => fn({}, ...args));
        if (task.run !== "install") {
            setTimeout(() => {
                fire("Succeeded");
                fire("Stopped");
            }, 10);
            return;
        }
        const speed = options.installSpeed ?? 1;
        const timeline = [
            [0, "ENVIRONMENT_CONFIGURATION", 0, "Setting up the installation environment"],
            [400, "STORAGE_CONFIGURATION", 2, "Creating disklabel on /dev/nvme0n1"],
            [900, null, 5, "Creating luks on /dev/nvme0n1p3"],
            [1500, "SOFTWARE_INSTALLATION", 8, "Starting package installation process"],
            [2200, null, 9, "Receiving objects: 35% (4211/12031) 612 MB"],
            [3000, null, 9, "Receiving objects: 78% (9384/12031) 1.4 GB"],
            [3800, "STORAGE_CONFIGURATION", 12, "Writing storage configuration"],
            [4200, "BOOTLOADER_INSTALLATION", 14, "Installing boot loader"],
            [4800, "SYSTEM_CONFIGURATION", 18, "Configuring installed system"],
            [5600, null, 26, "Creating users"],
            [6200, "BOOTLOADER_INSTALLATION", 30, "Generating initramfs"],
            [6800, "SYSTEM_CONFIGURATION", 36, "Running post-installation scripts"],
        ];
        timeline.forEach(([at, category, step, message]) => setTimeout(() => {
            if (category) {
                (task.listeners.CategoryChanged || []).forEach(fn => fn({}, category));
            }
            fire("ProgressChanged", step, message);
        }, at * speed));
        setTimeout(() => {
            if (options.installOutcome === "fail") {
                fire("Failed");
                fire("Stopped");
                return;
            }
            fire("ProgressChanged", 40, "");
            fire("Succeeded");
            fire("Stopped");
        }, 7400 * speed);
    };

    const proxy = (iface, path) => {
        const task = tasks[path] || { listeners: {} };
        return {
            addEventListener: (signal, fn) => {
                task.listeners[signal] = [...(task.listeners[signal] || []), fn];
            },
            Finish: () => {
                task.finishCalls = (task.finishCalls || 0) + 1;
                if (typeof window !== "undefined" && task.run === "install") {
                    window.__atlasMockFinishCalls = (window.__atlasMockFinishCalls || 0) + 1;
                }
                return options.installOutcome === "fail" && task.run === "install" && task.finishCalls === 1
                    ? Promise.reject(new Error(options.installError || "mock: installation failed")) : Promise.resolve();
            },
            GetResult: () => Promise.resolve(task.result),
            Start: () => {
                runTask(path);
                return Promise.resolve();
            },
            wait: (callback) => {
                setTimeout(() => {
                    callback();
                    if (options.resumeFailure && iface === "org.fedoraproject.Anaconda.Task") {
                        for (const listener of task.listeners.Stopped || []) listener();
                    }
                }, 0);
                return Promise.resolve();
            },
        };
    };

    return {
        call,
        proxy,
        subscribe: (match, callback) => {
            listeners.push(callback);
            return { remove: () => listeners.splice(listeners.indexOf(callback), 1) };
        },
    };
};
