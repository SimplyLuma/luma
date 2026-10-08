/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 1 · What language should Luma speak?
 * Anaconda's Localization module owns the data. The locale and keyboard calls
 * are the ones anaconda-webui 68 makes (InstallationLanguage.jsx, Keyboard.jsx,
 * non-GNOME path).
 */
import React, { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { setLocale } from "../apis/boss.js";
import { setCompositorLayouts, setLanguage, setXKeyboardDefaults, setXLayouts } from "../apis/localization.js";

import { getKeyboardConfigurationAction } from "../actions/localization-actions.js";

import { error as logError } from "../helpers/log.js";
import {
    allLanguageRows, detectedLocaleFromPlatformLang, matchesLanguageSearch, pickLanguageRows,
} from "../model/languages.js";

import { LanguageContext, ProbeContext } from "../contexts/Common.jsx";

import {
    Actions, Button, MoreButton, Option, Options, SelectField, Spacer, Step, TextField, Warn,
} from "../atlas/components.jsx";

const v = (value) => (value && typeof value === "object" && "v" in value) ? value.v : value;

const langAttr = (localeId) => (localeId || "").split(".")[0].replace("_", "-");

export const LanguageStep = ({ continueRef, dispatch, index, next, panelRef }) => {
    const { commonLocales, keyboardLayouts, language, languages, plannedVconsole, plannedXlayouts } = useContext(LanguageContext);
    const probe = useContext(ProbeContext);
    const [showAll, setShowAll] = useState(false);
    const [search, setSearch] = useState("");
    const [problem, setProblem] = useState(null);
    const [pending, setPending] = useState(null);
    const detectionApplied = useRef(false);

    const detected = useMemo(
        () => detectedLocaleFromPlatformLang(languages, probe?.platformLang),
        [languages, probe?.platformLang]
    );

    const choose = useCallback(async (localeId) => {
        setPending(localeId);
        setProblem(null);
        try {
            await setLanguage({ lang: localeId });
            await setLocale({ locale: localeId });
            // As anaconda-webui does outside GNOME: let the backend pick the
            // layouts that suit the new language, then read them back.
            await setXKeyboardDefaults();
            dispatch(getKeyboardConfigurationAction());
        } catch (exception) {
            logError("atlas: setting the language failed", exception?.message);
            setProblem("Luma could not switch to that language. Try again, or pick another.");
        } finally {
            setPending(null);
        }
    }, [dispatch]);

    // A genuinely detected language is chosen for the person, once, unless
    // they asked for a language on the boot line.
    useEffect(() => {
        if (detectionApplied.current || !detected || !language) {
            return;
        }
        detectionApplied.current = true;
        if (detected !== language && !probe?.bootOptions?.lang) {
            choose(detected);
        }
    }, [choose, detected, language, probe?.bootOptions?.lang]);

    const rows = useMemo(
        () => pickLanguageRows({ commonLocales, current: pending || language, detected, languages }),
        [commonLocales, detected, language, languages, pending]
    );
    const everyRow = useMemo(
        () => (showAll ? allLanguageRows({ detected, languages }) : []),
        [detected, languages, showAll]
    );
    const filtered = everyRow.filter(row => matchesLanguageSearch(row, search));
    const selected = pending || language;

    const layoutId = plannedXlayouts?.[0];
    const layoutGroups = useMemo(() => {
        const toOption = layout => ({ label: v(layout.description) || v(layout["layout-id"]), value: v(layout["layout-id"]) });
        const common = keyboardLayouts.filter(layout => v(layout["is-common"])).map(toOption);
        const other = keyboardLayouts.filter(layout => !v(layout["is-common"])).map(toOption)
                .sort((a, b) => a.label.localeCompare(b.label));
        if (layoutId && ![...common, ...other].some(option => option.value === layoutId)) {
            common.unshift({ label: layoutId, value: layoutId });
        }
        return [{ label: "Suggested", options: common }, { label: "Other layouts", options: other }];
    }, [keyboardLayouts, layoutId]);

    const chooseLayout = async (id) => {
        setProblem(null);
        try {
            await setCompositorLayouts({ layouts: [id] }).catch(exception => {
                // The installer session may have no compositor keymap control;
                // the planned layout for the installed system still applies.
                logError("atlas: compositor layout change failed", exception?.message);
            });
            await setXLayouts({ layouts: [id] });
            dispatch(getKeyboardConfigurationAction());
        } catch (exception) {
            logError("atlas: setting the keyboard layout failed", exception?.message);
            setProblem("Luma could not use that keyboard layout. Pick another.");
        }
    };

    const keyboardProblem = plannedVconsole === ""
        ? "This layout cannot be used when the computer starts. Pick another."
        : null;
    const valid = !!language && !pending && !!layoutId && !keyboardProblem;

    const onContinue = () => valid && next();
    continueRef.current = onContinue;

    const renderRow = (row, position, prefix) => (
        <Option
          dataFirst={prefix === "short" && row.localeId === selected}
          id={`language-${prefix}-${row.localeId.replace(/\W/g, "-")}`}
          key={`${prefix}-${row.localeId}`}
          lang={langAttr(row.localeId)}
          onSelect={() => choose(row.localeId)}
          pressed={row.localeId === selected}
          subtitle={row.region || null}
          tag={row.tag}
          title={row.name}
        />
    );

    return (
        <Step
          actions={(
              <Actions>
                  <Spacer />
                  <Button variant="primary" arrow disabled={!valid} onClick={onContinue}>Continue</Button>
              </Actions>
          )}
          id="language"
          index={index}
          panelRef={panelRef}
          title="What language should Luma speak?"
        >
            <Options labelledBy="language-title">
                {rows.map((row, position) => renderRow(row, position, "short"))}
            </Options>
            <MoreButton
              controls="language-all"
              expanded={showAll ? "true" : "false"}
              onClick={() => setShowAll(!showAll)}
            >
                {showAll ? "Show fewer languages" : "More languages"}
            </MoreButton>
            {showAll && (
                <div id="language-all" className="install-group">
                    <TextField
                      id="language-search"
                      label="Find a language"
                      value={search}
                      onChange={setSearch}
                      autoFocus
                    />
                    <div className="install-scroll" role="group" aria-label="All languages">
                        <Options>
                            {filtered.map((row, position) => renderRow(row, position, "all"))}
                        </Options>
                    </div>
                </div>
            )}
            {problem && <Warn live strong={problem} />}
            <SelectField
              groups={layoutGroups}
              id="language-keyboard"
              label="Keyboard layout"
              onChange={chooseLayout}
              problem={keyboardProblem}
              value={layoutId || ""}
            />
        </Step>
    );
};
