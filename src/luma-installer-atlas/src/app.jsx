/*
 * Copyright (C) 2021 Red Hat, Inc.
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * The Atlas wizard. Backend start-up (bus address, backend-ready flag, API
 * client initialisation) is anaconda-webui 68's src/components/app.jsx; the
 * room above it is Luma's.
 */
import cockpit from "cockpit";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { clients } from "./apis/index.js";
import { getPayloadSource } from "./apis/payload_source.js";

import { initialState, reducer, useReducerWithThunk } from "./reducer.js";

import { readConf } from "./helpers/conf.js";
import { error as logError } from "./helpers/log.js";

import { MainContextWrapper } from "./contexts/Common.jsx";

import { Dots, StepCountContext, VisuallyHidden } from "./atlas/components.jsx";
import { ErrorRoom } from "./steps/ErrorRoom.jsx";
import { STEPS } from "./steps/index.js";
import { probePayload, probeSystem } from "./system/host.js";
import { loadSession, saveSession } from "./system/session.js";
import { installerAppearance } from "./model/appearance.js";
import { startViewerHeartbeat } from "./system/viewer.js";
import { restoreEncryptionChoice } from "./model/encryption.js";

const N_ = cockpit.noop;

/* Step indices, Fedora 44's order one for one. */
export const STEP = {
    ACCOUNT: 4,
    APPS: 5,
    DESTINATION: 2,
    DONE: 8,
    INSTALLING: 7,
    LANGUAGE: 0,
    LOCK: 3,
    REVIEW: 6,
    TIME: 1,
};

const useAddress = (onCritFail) => {
    const [backendReady, setBackendReady] = useState(false);
    const [address, setAddress] = useState();
    const wasReadyRef = useRef(false);

    useEffect(() => {
        if (!backendReady) {
            return;
        }
        const file = cockpit.file("/run/anaconda/bus.address");
        file.watch(content => setAddress(content));
        return () => file.close();
    }, [backendReady]);

    useEffect(() => {
        const file = cockpit.file("/run/anaconda/backend_ready");
        file.watch(content => {
            const isReady = content !== null;
            if (isReady) {
                wasReadyRef.current = true;
            }
            setBackendReady(isReady);
            if (!isReady && wasReadyRef.current) {
                onCritFail({ message: "The installer stopped unexpectedly." });
            }
        });
        return () => file.close();
    }, [onCritFail]);

    return address;
};

/* Ink by default. Paper for an explicit light environment; Slate at full
 * contrast when it asks for more contrast. A boot option wins over both:
 * inst.luma.surface=paper|ink|slate|contrast. */
const useSurface = (bootOptions) => {
    const query = (text) => (typeof window.matchMedia === "function" ? window.matchMedia(text) : null);
    const read = useCallback(() => {
        const params = new URLSearchParams(window.location.search);
        return installerAppearance({ override: params.get("surface"), bootOptions,
            contrast: query("(prefers-contrast: more)")?.matches,
            light: query("(prefers-color-scheme: light)")?.matches });
    }, [bootOptions]);

    const [surface, setSurface] = useState(read);
    useEffect(() => {
        setSurface(read());
        const queries = [query("(prefers-contrast: more)"), query("(prefers-color-scheme: light)")].filter(Boolean);
        const update = () => setSurface(read());
        queries.forEach(q => q.addEventListener?.("change", update));
        return () => queries.forEach(q => q.removeEventListener?.("change", update));
    }, [read]);
    return surface;
};

export const Application = () => {
    const startupState = useMemo(() => restoreEncryptionChoice(initialState, loadSession().answers), []);
    const [state, dispatch] = useReducerWithThunk(reducer, startupState);
    const [fatal, setFatal] = useState(null);
    const [conf, setConf] = useState();
    const [probe, setProbe] = useState(null);
    const [storeReady, setStoreReady] = useState(false);

    const onCritFail = useCallback((exception) => {
        logError("atlas: critical failure", exception?.message || String(exception));
        setFatal(current => current || exception || { message: "Unknown error" });
    }, []);
    const critical = useCallback((context) => (exception) => onCritFail({ context, ...(exception || {}), message: exception?.message || String(exception) }), [onCritFail]);

    const address = useAddress(onCritFail);

    useEffect(() => {
        readConf().then(setConf, critical(N_("Reading the installer configuration failed.")));
    }, [critical]);

    useEffect(() => {
        let cancelled = false;
        probeSystem()
                .then(result => !cancelled && setProbe(current => ({ ...(current || {}), ...result, ready: true })))
                .catch(exception => {
                    logError("atlas: probe failed", exception?.message);
                    if (!cancelled) {
                        setProbe(current => ({ ...(current || {}), battery: null, installerDisks: [], platformLang: null, probeFailed: true, ready: true, stage2InMemory: false }));
                    }
                });
        return () => { cancelled = true };
    }, []);

    useEffect(() => {
        if (!address) {
            return;
        }
        Promise.all(clients.map(Client => new Client(address, dispatch).init()))
                .then(() => setStoreReady(true), critical(N_("Reading information about the computer failed.")));
    }, [address, critical, dispatch]);

    // What is being installed, from the active payload source.
    useEffect(() => {
        if (!storeReady) {
            return;
        }
        getPayloadSource()
                .then(async source => {
                    const details = source.type === "RPM_OSTREE" && source.url && source.ref
                        ? await probePayload({ ref: source.ref, url: source.url }).catch(() => ({}))
                        : {};
                    setProbe(current => ({ ...(current || {}), payload: { ...source, ...details } }));
                })
                .catch(exception => logError("atlas: reading the payload source failed", exception?.message));
    }, [storeReady]);

    const surface = useSurface(probe?.bootOptions);

    const ready = !!(fatal || (conf && address && storeReady && probe?.ready));
    useEffect(() => ready ? startViewerHeartbeat() : undefined, [ready]);

    let content;
    if (fatal) {
        content = <ErrorRoom exception={fatal} />;
    } else if (!conf || !address || !storeReady || !probe?.ready) {
        content = (
            <div className="install-startup" role="status">
                <img src="loader-background.svg" width="142" height="75" alt="" />
                <VisuallyHidden>Getting ready</VisuallyHidden>
            </div>
        );
    } else {
        content = <Wizard dispatch={dispatch} onCritFail={onCritFail} />;
    }

    return (
        <MainContextWrapper state={state} conf={conf} probe={probe}>
            <div
              className="install-stage"
              data-install-surface={surface.surface}
              data-install-contrast={surface.contrast || undefined}
              data-install-concept="atlas"
            >
                {content}
            </div>
        </MainContextWrapper>
    );
};

/* Arrow keys belong to text fields, selects and date/time inputs; a switch
 * or an option row leaves them to step navigation. */
const isTypingTarget = (element) => !!element && (
    /^(TEXTAREA|SELECT)$/.test(element.tagName) ||
    (element.tagName === "INPUT" && !["checkbox", "radio", "button"].includes(element.type)) ||
    element.isContentEditable
);

export const Wizard = ({ dispatch, onCritFail }) => {
    const restored = useMemo(() => loadSession(), []);
    const [current, setCurrent] = useState(restored.current ?? STEP.LANGUAGE);
    const [reached, setReached] = useState(restored.reached ?? STEP.LANGUAGE);
    const [announcement, setAnnouncement] = useState("");
    const [answers, setAnswers] = useState(restored.answers || { autologin: false, timeAuto: null });

    useEffect(() => {
        saveSession({ answers, current, reached });
    }, [answers, current, reached]);
    const panelRef = useRef(null);
    const bodyRef = useRef(null);
    const continueRef = useRef(null);

    const step = STEPS[current];
    const locked = current >= STEP.INSTALLING;

    const go = useCallback((index) => {
        setCurrent(index);
        setReached(value => Math.max(value, index));
    }, []);

    /* A step whose answers change makes the steps after it unreachable again
     * until it is continued, so nothing downstream reads a stale plan. */
    const invalidateAfter = useCallback((index) => {
        setReached(value => Math.min(value, index));
    }, []);

    const next = useCallback(() => go(Math.min(current + 1, STEPS.length - 1)), [current, go]);
    const back = useCallback(() => setCurrent(Math.max(current - 1, 0)), [current]);

    // Focus lands on the first option when a step opens (handoff §5), and the
    // kicker and title are announced.
    useEffect(() => {
        const text = `Step ${current + 1} of ${STEPS.length}. ${STEPS[current].title}`;
        setAnnouncement(text);
        if (bodyRef.current) {
            bodyRef.current.scrollTop = 0;
        }
        const focusFirst = () => {
            const panel = panelRef.current;
            if (!panel) {
                return;
            }
            const target = panel.querySelector("[data-first-control]:not([disabled])") ||
                panel.querySelector(".install-option:not([aria-disabled='true']), input:not([disabled]), select:not([disabled])");
            (target || panel).focus({ preventScroll: true });
        };
        const timer = setTimeout(focusFirst, 30);
        return () => clearTimeout(timer);
    }, [current]);

    // → and ← move between steps; never destructive, never while typing.
    useEffect(() => {
        const onKey = (event) => {
            if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || isTypingTarget(document.activeElement)) {
                return;
            }
            if (event.key === "ArrowRight" && current < STEP.REVIEW) {
                event.preventDefault();
                continueRef.current?.();
            } else if (event.key === "ArrowLeft" && current > STEP.LANGUAGE && current !== STEP.INSTALLING) {
                event.preventDefault();
                back();
            }
        };
        document.addEventListener("keydown", onKey);
        return () => document.removeEventListener("keydown", onKey);
    }, [back, current]);

    const names = useMemo(() => STEPS.map(s => s.name), []);
    const Component = step.component;

    return (
        <StepCountContext.Provider value={STEPS.length}>
            <div className="install-body" ref={bodyRef}>
                <div className="install-panels">
                    <Component
                      answers={answers}
                      back={back}
                      continueRef={continueRef}
                      dispatch={dispatch}
                      go={go}
                      index={current}
                      invalidateAfter={invalidateAfter}
                      key={step.id}
                      next={next}
                      onCritFail={onCritFail}
                      panelRef={panelRef}
                      reached={reached}
                      setAnswers={setAnswers}
                    />
                </div>
            </div>
            <Dots
              current={current}
              locked={locked}
              names={names}
              onGo={index => setCurrent(index)}
              reached={reached}
            />
            <div className="install-visually-hidden" aria-live="polite" role="status">{announcement}</div>
        </StepCountContext.Provider>
    );
};
