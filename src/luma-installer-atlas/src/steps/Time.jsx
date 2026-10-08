/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 2 · Where in the world are you?
 * Anaconda's Timezone module owns the zone and NTP; the calls are those of
 * anaconda-webui 68 (DateAndTime.jsx, Timezone.jsx). "Set my time
 * automatically" is NTP: it uses the network connection, not the location.
 * Geolocation is switched off in Luma's installer profile, so nothing about
 * the person's location is sent anywhere.
 */
import cockpit from "cockpit";

import React, { useContext, useEffect, useMemo, useRef, useState } from "react";

import { getNTPEnabled, getSystemDateTime, getTimezone, setNTPEnabled, setSystemDateTime, setTimezone } from "../apis/timezone.js";

import { convertToCockpitLang } from "../helpers/language.js";
import { error as logError } from "../helpers/log.js";
import { clockReading, confirmManualClock, isoInZone, zoneParts } from "../model/clock.js";
import { confirmLocation } from "../model/location.js";
import { recordLocationIntent } from "../system/host.js";
import { displayCity, displayRegion } from "../model/format.js";

import { LanguageContext, NetworkContext, TimezoneContext } from "../contexts/Common.jsx";

import {
    Actions, Button, DateTimeField, Note, Pair, SelectField, Spacer, Step, SwitchRow,
} from "../atlas/components.jsx";

const useNow = (interval = 15000) => {
    const [now, setNow] = useState(() => new Date());
    useEffect(() => {
        const timer = setInterval(() => setNow(new Date()), interval);
        return () => clearInterval(timer);
    }, [interval]);
    return [now, setNow];
};

export const TimeStep = ({ answers, back, continueRef, index, next, panelRef, setAnswers }) => {
    const { allValidTimezones, timezone } = useContext(TimezoneContext);
    const { connected } = useContext(NetworkContext);
    const { language } = useContext(LanguageContext);
    const [now, setNow] = useNow();
    const [manual, setManual] = useState(() => zoneParts({ zone: timezone || "UTC" }));
    const manualDraft = useRef(manual);
    const acknowledged = useRef(null);
    const clockQueue = useRef(Promise.resolve());
    const clockWrite = useRef(null);
    const [clockPending, setClockPending] = useState(false);
    const [problem, setProblem] = useState(null);
    const resetManual = (value) => { manualDraft.current = value; acknowledged.current = null; setManual(value); };
    const editManual = (patch) => {
        manualDraft.current = { ...manualDraft.current, ...patch };
        setManual(manualDraft.current);
        setProblem(null);
    };
    const [pending, setPending] = useState(false);
    const writing = useRef(false);
    const selectedZone = useRef(null);
    const begin = () => {
        if (writing.current) return false;
        writing.current = true;
        setPending(true);
        setProblem(null);
        return true;
    };
    const finish = () => { writing.current = false; setPending(false); };
    // Before a manual edit, the fields must describe the chosen city's time,
    // rather than the UTC fields mounted before Anaconda loaded its zone.
    useEffect(() => resetManual(zoneParts({ zone: timezone || "UTC" })), [timezone]);

    const auto = answers.timeAuto;
    const offline = connected === false;

    // Automatic time is a persistent preference, not current connectivity.
    // An offline installation must still synchronize after its first connection.
    useEffect(() => {
        if (auto !== null) {
            return;
        }
        // Serialize initialization with manual changes so a late read cannot
        // turn automatic time back on after the person switches it off.
        if (!begin()) return;
        getNTPEnabled()
                .then(async enabled => {
                    if (!enabled) await setNTPEnabled({ enabled: true });
                    if (!await getNTPEnabled()) throw new Error("Automatic time readback differs");
                    setAnswers(current => current.timeAuto === null ? { ...current, timeAuto: true } : current);
                })
                .catch(exception => {
                    logError("atlas: reading NTP failed", exception?.message);
                    setProblem("Luma could not enable automatic time. Try again.");
                })
                .finally(finish);
    }, [auto, setAnswers]);

    const [region, city] = useMemo(() => {
        if (timezone && timezone.includes("/")) {
            const [first, ...rest] = timezone.split("/");
            return [first, rest.join("/")];
        }
        return ["", ""];
    }, [timezone]);

    const regions = useMemo(
        () => Object.keys(allValidTimezones || {})
                .sort((a, b) => displayRegion(a).localeCompare(displayRegion(b)))
                .map(value => ({ label: displayRegion(value), value })),
        [allValidTimezones]
    );
    const cities = useMemo(
        () => [...(allValidTimezones?.[region] || [])]
                .sort((a, b) => displayCity(a).localeCompare(displayCity(b)))
                .map(value => ({ label: displayCity(value), value })),
        [allValidTimezones, region]
    );

    const chooseZone = async (zone) => {
        if (!begin()) return;
        selectedZone.current = zone;
        try {
            await setTimezone({ timezone: zone });
            if (await getTimezone() !== zone) throw new Error("Time zone readback differs");
        } catch (exception) {
            logError("atlas: setting the time zone failed", exception?.message);
            setProblem("Luma could not use that city. Pick another.");
        } finally { finish(); }
    };

    const onRegion = (value) => {
        const list = allValidTimezones?.[value] || [];
        const keep = list.includes(city) ? city : [...list].sort((a, b) => displayCity(a).localeCompare(displayCity(b)))[0];
        if (keep) {
            chooseZone(`${value}/${keep}`);
        }
    };

    const onAuto = async (value) => {
        if (!begin()) return;
        setAnswers(current => ({ ...current, timeAuto: value }));
        try {
            await setNTPEnabled({ enabled: value });
        } catch (exception) {
            logError("atlas: setting NTP failed", exception?.message);
            setProblem("Luma could not change automatic time. Try again.");
        } finally { finish(); }
        if (!value) {
            resetManual(zoneParts({ zone: timezone || "UTC" }));
        }
    };

    // Blur starts a write, while Continue can arrive in the same event turn.
    // Draft refs retain the final keystroke; the queue makes both paths await
    // the same acknowledged write and prevents an older edit winning a race.
    const applyManual = () => {
        const draft = { ...manualDraft.current, zone: timezone || "UTC" };
        const spec = isoInZone(draft);
        if (!spec) { setProblem("Choose a valid date (YYYY-MM-DD) and time (HH:MM)."); return Promise.resolve(false); }
        if (!clockWrite.current && acknowledged.current === spec) return clockQueue.current.then(() => true);
        if (clockWrite.current?.spec === spec) return clockWrite.current.promise;
        setClockPending(true);
        const promise = clockQueue.current.then(async () => {
            try {
                await confirmManualClock({ ...draft, setSystemDateTime, getSystemDateTime });
                acknowledged.current = spec;
                if (isoInZone({ ...manualDraft.current, zone: timezone || "UTC" }) === spec) setProblem(null);
                setNow(new Date(spec));
                return true;
            } catch (exception) {
                logError("atlas: setting the clock failed", exception?.message);
                if (isoInZone({ ...manualDraft.current, zone: timezone || "UTC" }) === spec) setProblem(exception.message);
                return false;
            } finally {
                if (clockWrite.current?.promise === promise) { clockWrite.current = null; setClockPending(false); }
            }
        });
        clockWrite.current = { spec, promise };
        clockQueue.current = promise;
        return promise;
    };

    const locale = convertToCockpitLang({ lang: language || cockpit.language || "en_US" });
    const reading = clockReading({ locale, now, zone: timezone });
    const valid = !!timezone && auto !== null && !pending && !problem && (auto || !!isoInZone({ ...manual, zone: timezone }));
    const onContinue = async () => {
        if (!valid || !begin()) return;
        try {
            if (!auto && !await applyManual()) return;
            await confirmLocation({ expected: selectedZone.current || timezone, getTimezone, record: recordLocationIntent });
            next();
        } catch (exception) {
            logError("atlas: city confirmation failed", exception?.message);
            setProblem("Luma could not save that city. Choose it again.");
        } finally { finish(); }
    };
    continueRef.current = onContinue;

    return (
        <Step
          actions={(
              <Actions>
                  <Button variant="quiet" onClick={back}>Back</Button>
                  <Spacer />
                  <Button variant="primary" arrow disabled={!valid} onClick={onContinue}>Continue</Button>
              </Actions>
          )}
          id="location"
          index={index}
          panelRef={panelRef}
          title="Where in the world are you?"
        >
            <SwitchRow
              checked={!!auto}
              dataFirst
              disabled={pending || clockPending}
              id="location-auto-time"
              onChange={onAuto}
              subtitle={offline
                  ? "Synchronizes when this computer connects to the internet"
                  : "Uses your network connection, not your location"}
              title="Set my time automatically"
            />
            <Pair>
                <SelectField disabled={pending || clockPending} id="location-region" label="Region" onChange={onRegion} options={regions} value={region} />
                <SelectField disabled={pending || clockPending} id="location-city" label="City" onChange={value => chooseZone(`${region}/${value}`)} options={cities} value={city} />
            </Pair>
            {auto === false && (
                <Pair>
                    <DateTimeField disabled={pending} id="location-date" label="Date" type="date" value={manual.date} onChange={date => editManual({ date })} onCommit={applyManual} />
                    <DateTimeField disabled={pending} id="location-time" label="Time" type="time" value={manual.time} onChange={time => editManual({ time })} onCommit={applyManual} />
                </Pair>
            )}
            {reading && (
                <Note>
                    Your clock will read <b>{reading.time}</b> on {reading.date}.
                </Note>
            )}
            {problem && <Note live>{problem}</Note>}
        </Step>
    );
};
