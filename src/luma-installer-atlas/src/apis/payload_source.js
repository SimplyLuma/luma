/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * The active payload's source, read from Anaconda's Payloads module so the
 * review step can say what is being installed. Written in the style of the
 * anaconda-webui 68 API clients it sits beside.
 */
import { _getProperty } from "./helpers.js";

import { getActivePayload, PayloadsClient } from "./payloads.js";

const PAYLOAD_INTERFACE = "org.fedoraproject.Anaconda.Modules.Payloads.Payload";
const SOURCE_INTERFACE = "org.fedoraproject.Anaconda.Modules.Payloads.Source";
const RPM_OSTREE_SOURCE_INTERFACE = "org.fedoraproject.Anaconda.Modules.Payloads.Source.RPMOSTree";

/**
 * @returns {Promise<{type: string|null, url?: string, ref?: string, osname?: string}>}
 */
export const getPayloadSource = async () => {
    const payload = await getActivePayload();
    if (!payload) {
        return { type: null };
    }
    const sources = await _getProperty(PayloadsClient, payload, PAYLOAD_INTERFACE, "Sources");
    if (!sources?.length) {
        return { type: null };
    }
    const type = await _getProperty(PayloadsClient, sources[0], SOURCE_INTERFACE, "Type");
    if (type !== "RPM_OSTREE") {
        return { type };
    }
    const configuration = await _getProperty(PayloadsClient, sources[0], RPM_OSTREE_SOURCE_INTERFACE, "Configuration");
    return {
        osname: configuration.osname?.v,
        ref: configuration.ref?.v,
        remote: configuration.remote?.v,
        type,
        url: configuration.url?.v,
    };
};
