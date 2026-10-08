/* Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 * Shared Atlas date/time control. All calendar UI stays in the DOM: native
 * Chromium date popups can terminate the installer's Qt Quick viewer.
 */
import React, { useEffect, useRef, useState } from "react";

const pad = value => String(value).padStart(2, "0");
const isoDate = date => `${String(date.getUTCFullYear()).padStart(4, "0")}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
const parsedDate = value => {
    const date = /^\d{4}-\d{2}-\d{2}$/.test(value || "") ? new Date(`${value}T12:00:00Z`) : null;
    return date && Number.isFinite(date.getTime()) && isoDate(date) === value ? date : null;
};
const utcDate = (year, month, day) => { const date = new Date(0); date.setUTCFullYear(year, month, day); date.setUTCHours(12, 0, 0, 0); return date; };
const shifted = (date, days) => new Date(date.getTime() + days * 86400000);

export const DateTimeField = ({ disabled, id, label, onChange, onCommit, type, value }) => {
    const [open, setOpen] = useState(false);
    const [focusDate, setFocusDate] = useState(() => isoDate(parsedDate(value) || new Date()));
    const [month, setMonth] = useState(() => {
        const date = parsedDate(value) || new Date();
        return [date.getUTCFullYear(), date.getUTCMonth()];
    });
    const control = useRef(null);
    const input = useRef(null);
    const calendar = useRef(null);
    const focusDay = useRef(false);
    const first = utcDate(month[0], month[1], 1);
    const title = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric", timeZone: "UTC" }).format(first);
    const count = utcDate(month[0], month[1] + 1, 0).getUTCDate();
    const cells = [...Array((first.getUTCDay() + 6) % 7).fill(null), ...Array.from({ length: count }, (_, n) => n + 1)];
    while (cells.length % 7) cells.push(null);
    useEffect(() => {
        if (!open) return undefined;
        const outside = event => { if (!control.current?.contains(event.target)) setOpen(false); };
        document.addEventListener("pointerdown", outside);
        return () => document.removeEventListener("pointerdown", outside);
    }, [open]);
    useEffect(() => {
        if (open && focusDay.current) calendar.current?.querySelector(`[data-date="${focusDate}"]`)?.focus();
    }, [open, focusDate, month]);
    const toggle = () => {
        if (open) { setOpen(false); input.current?.focus(); return; }
        const date = parsedDate(value) || new Date();
        setMonth([date.getUTCFullYear(), date.getUTCMonth()]);
        setFocusDate(isoDate(date)); focusDay.current = true; setOpen(true);
    };
    const select = date => {
        onChange?.(date); setOpen(false); input.current?.focus(); onCommit?.();
    };
    const moveMonth = amount => {
        const next = utcDate(month[0], month[1] + amount, 1);
        if (next.getUTCFullYear() < 1 || next.getUTCFullYear() > 9999) return;
        focusDay.current = false; setMonth([next.getUTCFullYear(), next.getUTCMonth()]); setFocusDate(isoDate(next));
    };
    const navigate = (event, date) => {
        if (event.key === "Escape") { event.preventDefault(); setOpen(false); input.current?.focus(); return; }
        const days = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7,
            Home: -((date.getUTCDay() + 6) % 7), End: 6 - ((date.getUTCDay() + 6) % 7) };
        let next;
        if (Object.hasOwn(days, event.key)) next = shifted(date, days[event.key]);
        if (event.key === "PageUp" || event.key === "PageDown") next = utcDate(date.getUTCFullYear(), date.getUTCMonth() + (event.key === "PageUp" ? -1 : 1), 1);
        if (next && next.getUTCFullYear() >= 1 && next.getUTCFullYear() <= 9999) {
            event.preventDefault(); focusDay.current = true;
            setMonth([next.getUTCFullYear(), next.getUTCMonth()]); setFocusDate(isoDate(next));
        }
    };
    return (
        <div className="install-field" ref={control} onBlur={event => {
            if (!event.currentTarget.contains(event.relatedTarget)) { setOpen(false); onCommit?.(); }
        }}>
            <label htmlFor={id}>{label}</label>
            <div className="install-datetime-input">
                <input id={id} ref={input} type="text" inputMode="numeric" value={value} disabled={disabled}
                  placeholder={type === "date" ? "YYYY-MM-DD" : "HH:MM"}
                  aria-describedby={`${id}-format`} autoComplete="off" spellCheck={false}
                  onChange={event => onChange?.(event.target.value)}
                  onKeyDown={event => {
                      if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); onCommit?.(); }
                      if (event.key === "ArrowDown" && event.altKey && type === "date") { event.preventDefault(); toggle(); }
                      if (event.key === "Escape" && open) { event.preventDefault(); setOpen(false); }
                  }} />
                {type === "date" && <button type="button" className="install-calendar-toggle" disabled={disabled}
                  aria-label="Choose a date" aria-expanded={open} aria-controls={`${id}-calendar`} onClick={toggle}>
                    <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 11h18" /></svg>
                </button>}
            </div>
            <small id={`${id}-format`} className="install-note">{type === "date" ? "YYYY-MM-DD" : "HH:MM (24-hour time)"}</small>
            {open && <div id={`${id}-calendar`} className="install-calendar" role="dialog" aria-label="Choose a date" onKeyDown={event => {
                if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); input.current?.focus(); }
            }}>
                <div className="install-calendar-header">
                    <button type="button" aria-label="Previous month" onClick={() => moveMonth(-1)}>‹</button>
                    <strong>{title}</strong>
                    <button type="button" aria-label="Next month" onClick={() => moveMonth(1)}>›</button>
                </div>
                <div role="grid" aria-label={title} ref={calendar}>
                    <div role="row" className="install-calendar-row">
                        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(day => <span role="columnheader" key={day}>{day}</span>)}
                    </div>
                    {Array.from({ length: cells.length / 7 }, (_, row) => <div role="row" className="install-calendar-row" key={row}>
                        {cells.slice(row * 7, row * 7 + 7).map((day, column) => {
                            const date = day ? utcDate(month[0], month[1], day) : null;
                            const iso = date && isoDate(date);
                            return day ? <button type="button" role="gridcell" key={column} data-date={iso}
                              aria-label={new Intl.DateTimeFormat(undefined, { dateStyle: "full", timeZone: "UTC" }).format(date)}
                              aria-selected={iso === value} tabIndex={iso === focusDate ? 0 : -1}
                              onClick={() => select(iso)} onKeyDown={event => navigate(event, date)}>{day}</button>
                                : <span role="gridcell" key={column} />;
                        })}
                    </div>)}
                </div>
            </div>}
        </div>
    );
};
