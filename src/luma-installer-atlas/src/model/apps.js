/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * "Get more apps" (ADR-028 Depot, section 14). The installer carries no apps:
 * it records which collections the person wants, and Depot's first-boot
 * provisioning downloads them. Nothing is preselected.
 */

import { joinList } from "./format.js";

export const collectionRows = (catalog) => (catalog?.collections || []).map(collection => {
    const names = (collection.applications || []).map(id => catalog.applications?.[id]?.name || id);
    return {
        applications: collection.applications || [],
        id: collection.id,
        name: collection.name,
        subtitle: `${joinList(names)} · ${collection.summary}`,
    };
});

export const toggleCollection = (selected, id) => (selected.includes(id)
    ? selected.filter(item => item !== id)
    : [...selected, id]);

/** Keep only known collection ids, in catalog order. */
export const cleanSelection = (catalog, selected = []) => (catalog?.collections || [])
        .map(collection => collection.id)
        .filter(id => selected.includes(id));

/** The exact file Depot's first-boot provisioning reads. */
export const firstBootApps = (catalog, selected) => ({
    applications: [],
    collections: cleanSelection(catalog, selected),
});

export const reviewAppsText = (catalog, selected = []) => {
    const chosen = (catalog?.collections || []).filter(collection => selected.includes(collection.id));
    if (!chosen.length) {
        return "None";
    }
    return `${joinList(chosen.map(collection => collection.name))} · downloads when Luma first starts`;
};

export const selectionNote = (catalog, selected = []) => {
    const ids = [...new Set((catalog?.collections || [])
            .filter(collection => selected.includes(collection.id))
            .flatMap(collection => collection.applications))];
    if (!ids.length) {
        return null;
    }
    const names = ids.map(id => catalog.applications?.[id]?.name || id);
    return `${joinList(names)} will download the first time Luma starts with an internet connection.`;
};
