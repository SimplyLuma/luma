/*
 * Copyright (C) 2022 Red Hat, Inc.
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Derived from anaconda-webui 68 src/contexts/Common.jsx. Luma change: the
 * PatternFly popover helper and cockpit dialogs wrapper are removed; the
 * module contexts that the reused hooks and scenario checks read are kept
 * with the same names and shapes.
 */
import React, { createContext } from "react";

export const FooterContext = createContext(null);
export const LanguageContext = createContext(null);
export const OsReleaseContext = createContext(null);
export const PayloadContext = createContext(null);
export const RuntimeContext = createContext(null);
export const StorageContext = createContext(null);
export const StorageDefaultsContext = createContext(null);
export const SystemTypeContext = createContext(null);
export const TargetSystemRootContext = createContext(null);
export const UsersContext = createContext(null);
export const UserInterfaceContext = createContext(null);
export const NetworkContext = createContext(null);
export const TimezoneContext = createContext(null);
export const DialogsContext = createContext(null);
export const ProbeContext = createContext(null);

export const MainContextWrapper = ({ children, conf, probe, state }) => {
    const systemType = conf?.["Installation System"]?.type;
    const defaultScheme = conf?.Storage?.default_scheme;

    return (
        <LanguageContext.Provider value={state.localization}>
            <RuntimeContext.Provider value={state.runtime}>
                <StorageContext.Provider value={{ ...state.storage, isFetching: state.misc.isFetching }}>
                    <UsersContext.Provider value={state.users}>
                        <NetworkContext.Provider value={state.network}>
                            <PayloadContext.Provider value={state.payload}>
                                <TimezoneContext.Provider value={state.timezone}>
                                    <SystemTypeContext.Provider value={{ desktopVariant: "UNKNOWN", systemType }}>
                                        <StorageDefaultsContext.Provider value={{ defaultScheme }}>
                                            <TargetSystemRootContext.Provider value={conf?.["Installation Target"]?.system_root}>
                                                <UserInterfaceContext.Provider value={conf?.["User Interface"] || {}}>
                                                    <ProbeContext.Provider value={probe}>
                                                        {children}
                                                    </ProbeContext.Provider>
                                                </UserInterfaceContext.Provider>
                                            </TargetSystemRootContext.Provider>
                                        </StorageDefaultsContext.Provider>
                                    </SystemTypeContext.Provider>
                                </TimezoneContext.Provider>
                            </PayloadContext.Provider>
                        </NetworkContext.Provider>
                    </UsersContext.Provider>
                </StorageContext.Provider>
            </RuntimeContext.Provider>
        </LanguageContext.Provider>
    );
};
