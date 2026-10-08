/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 1 data rules. "Detected" is a promise: it may only be attached to a
 * locale that was derived from something real on this computer (today: the
 * firmware's PlatformLang variable). Anaconda's default language is never
 * called detected.
 */

const v = (value) => (value && typeof value === "object" && "v" in value) ? value.v : value;

export const localeId = (locale) => v(locale?.["locale-id"]);
export const localeNativeName = (locale) => v(locale?.["native-name"]) || "";
export const localeEnglishName = (locale) => v(locale?.["english-name"]) || "";
export const localeLanguageId = (locale) => v(locale?.["language-id"]) || (localeId(locale) || "").split("_")[0];

/** "Español (España)" -> { name: "Español", region: "España" } */
export const splitNativeName = (nativeName) => {
    const match = /^(.*?)\s*\((.*)\)\s*$/.exec(nativeName || "");
    if (!match) {
        return { name: nativeName || "", region: "" };
    }
    return { name: match[1], region: match[2] };
};

/**
 * Convert the UEFI PlatformLang value (RFC 4646, for example "en-US" or "fr")
 * into the language and territory parts Anaconda uses.
 */
export const parsePlatformLang = (value) => {
    if (!value || typeof value !== "string") {
        return null;
    }
    const cleaned = value.replace(/\0/g, "").trim();
    const match = /^([A-Za-z]{2,3})(?:[-_]([A-Za-z]{2}|[0-9]{3}))?/.exec(cleaned);
    if (!match) {
        return null;
    }
    return {
        language: match[1].toLowerCase(),
        territory: match[2] ? match[2].toUpperCase() : null,
    };
};

const allLocales = (languages) => Object.values(languages || {}).flatMap(language => language.locales || []);

/**
 * Find the Anaconda locale the firmware asks for, or null. A bare language
 * ("fr") matches that language's first locale only when the language has
 * exactly one; otherwise the territory is unknown and nothing is claimed.
 */
export const detectedLocaleFromPlatformLang = (languages, platformLang) => {
    const parsed = parsePlatformLang(platformLang);
    if (!parsed) {
        return null;
    }
    const locales = allLocales(languages).filter(locale => localeLanguageId(locale) === parsed.language);
    if (parsed.territory) {
        const exact = locales.find(locale => (localeId(locale) || "").startsWith(`${parsed.language}_${parsed.territory}.`) ||
            localeId(locale) === `${parsed.language}_${parsed.territory}`);
        return exact ? localeId(exact) : null;
    }
    return locales.length === 1 ? localeId(locales[0]) : null;
};

/*
 * Display order for the short list. This is presentation, not data: every
 * entry must also be in Anaconda's own common-locale list to be shown.
 */
export const PREFERRED_ORDER = [
    "en_US.UTF-8", "es_ES.UTF-8", "fr_FR.UTF-8", "de_DE.UTF-8", "ja_JP.UTF-8",
    "zh_CN.UTF-8", "pt_BR.UTF-8", "it_IT.UTF-8", "ru_RU.UTF-8", "ko_KR.UTF-8",
    "ar_EG.UTF-8", "hi_IN.UTF-8",
];

export const findLocale = (languages, id) => allLocales(languages).find(locale => localeId(locale) === id);

/**
 * The five rows on step 1: the current choice first, then Anaconda's common
 * locales, one row per language.
 */
export const pickLanguageRows = ({ commonLocales = [], count = 5, current, detected, languages }) => {
    const rows = [];
    const seenLanguages = new Set();

    const push = (id) => {
        const locale = findLocale(languages, id);
        if (!locale) {
            return;
        }
        const language = localeLanguageId(locale);
        if (seenLanguages.has(language) || rows.some(row => row.localeId === id)) {
            return;
        }
        seenLanguages.add(language);
        const { name, region } = splitNativeName(localeNativeName(locale));
        rows.push({
            englishName: localeEnglishName(locale),
            localeId: id,
            name,
            region,
            tag: detected && detected === id ? "Detected" : null,
        });
    };

    if (current) {
        push(current);
    }
    if (detected) {
        push(detected);
    }

    const common = new Set(commonLocales);
    PREFERRED_ORDER.filter(id => common.has(id)).forEach(id => rows.length < count && push(id));

    const rest = commonLocales
            .filter(id => !PREFERRED_ORDER.includes(id))
            .map(id => findLocale(languages, id))
            .filter(Boolean)
            .sort((a, b) => localeNativeName(a).localeCompare(localeNativeName(b)))
            .map(localeId);
    rest.forEach(id => rows.length < count && push(id));

    return rows.slice(0, Math.max(count, rows.findIndex(row => row.localeId === current) + 1));
};

/** Every locale, for the full list behind "More languages". */
export const allLanguageRows = ({ detected, languages }) => allLocales(languages)
        .map(locale => {
            const { name, region } = splitNativeName(localeNativeName(locale));
            return {
                englishName: localeEnglishName(locale),
                localeId: localeId(locale),
                name,
                region,
                tag: detected && detected === localeId(locale) ? "Detected" : null,
            };
        })
        .sort((a, b) => (a.name + a.region).localeCompare(b.name + b.region));

export const matchesLanguageSearch = (row, query) => {
    const needle = (query || "").trim().toLowerCase();
    if (!needle) {
        return true;
    }
    return [row.name, row.region, row.englishName, row.localeId]
            .some(field => (field || "").toLowerCase().includes(needle));
};
