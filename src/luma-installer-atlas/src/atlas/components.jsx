/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Atlas components. One question per screen: a kicker, a display title, a
 * lede, then controls in a single 640px column. Glyph paths are Lucide (ISC),
 * the same set the Design Center uses.
 */

import React, { createContext, useContext, useId, useState } from "react";

/* How many steps the wizard has; the kicker reads it. */
export const StepCountContext = createContext(8);

/* ── Glyphs ───────────────────────────────────────────────────────────────── */
export const CheckGlyph = () => (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 13 4 4L19 7" /></svg>
);

export const ArrowGlyph = () => (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></svg>
);

export const WarnGlyph = () => (
    <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 9v4" />
        <path d="M12 17h.01" />
        <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    </svg>
);

export const DriveGlyph = ({ kind }) => (kind === "usb" || kind === "removable")
    ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 20V7" /><path d="M8 11 12 7l4 4" /><rect x="8" y="20" width="8" height="2" rx="1" />
        </svg>
    )
    : (
        <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M22 12H2" />
            <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
            <path d="M6 16h.01M10 16h.01" />
        </svg>
    );

const EyeGlyph = ({ open }) => open
    ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0" />
            <circle cx="12" cy="12" r="3" />
        </svg>
    )
    : (
        <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c4.7 0 8.7 3 10 7a10.4 10.4 0 0 1-1.73 3.14" />
            <path d="M14.08 14.16A3 3 0 1 1 9.84 9.92" />
            <path d="M17.48 17.5A10.5 10.5 0 0 1 12 19c-4.7 0-8.7-3-10-7a10.4 10.4 0 0 1 4.5-5.46" />
            <path d="m2 2 20 20" />
        </svg>
    );

const ChevronGlyph = () => (
    <svg className="install-select-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
);

export const VisuallyHidden = ({ children, ...props }) => (
    <span className="install-visually-hidden" {...props}>{children}</span>
);

/* ── The step ─────────────────────────────────────────────────────────────── */
/**
 * The kicker is the only "where am I", and it is read first: the section is
 * labelled by kicker and title together.
 */
export const Step = ({ actions, children, id, index, lede, panelRef, title, total }) => {
    const count = useContext(StepCountContext);
    const steps = total || count;
    const kickerId = `${id}-kicker`;
    const titleId = `${id}-title`;
    return (
        <section
          className="install-panel"
          data-step={id}
          aria-labelledby={`${kickerId} ${titleId}`}
          ref={panelRef}
          tabIndex={-1}
        >
            <p className="install-kicker" id={kickerId}>{`Step ${index + 1} of ${steps}`}</p>
            <h1 className="install-title" id={titleId}>{title}</h1>
            {lede && <p className="install-lede">{lede}</p>}
            {children}
            {actions}
        </section>
    );
};

export const Actions = ({ children }) => <div className="install-actions">{children}</div>;
export const Spacer = () => <span className="install-spacer" />;

export const Button = ({ arrow, busy, children, disabled, onClick, variant = "", ...props }) => (
    <button
      type="button"
      className={`install-btn ${variant}`.trim()}
      aria-disabled={disabled || busy ? "true" : undefined}
      aria-busy={busy ? "true" : undefined}
      onClick={event => {
          if (disabled || busy) {
              event.preventDefault();
              return;
          }
          onClick?.(event);
      }}
      {...props}
    >
        {children}
        {arrow && <ArrowGlyph />}
    </button>
);

/* ── Options ──────────────────────────────────────────────────────────────── */
export const Group = ({ children, label, labelId }) => (
    <div className="install-group">
        {label && <p className="install-grouplabel" id={labelId}>{label}</p>}
        {children}
    </div>
);

export const Options = ({ children, compact, labelledBy }) => (
    <div className={compact ? "install-options compact" : "install-options"} role="group" aria-labelledby={labelledBy}>
        {children}
    </div>
);

/**
 * A real button with aria-pressed. Single-choice groups behave as radios
 * because the parent passes one pressed value. Disabled rows stay focusable
 * (aria-disabled) so a screen reader can hear why.
 */
export const Option = ({
    capacity,
    dataFirst,
    disabled,
    glyph,
    id,
    lang,
    onSelect,
    pressed,
    subtitle,
    tag,
    title,
}) => {
    const describedBy = `${id}-subtitle`;
    return (
        <button
          type="button"
          id={id}
          className="install-option"
          aria-pressed={pressed ? "true" : "false"}
          aria-disabled={disabled ? "true" : undefined}
          aria-describedby={subtitle ? describedBy : undefined}
          data-first-control={dataFirst ? "true" : undefined}
          onClick={() => !disabled && onSelect?.()}
        >
            {glyph && <span className="install-glyph">{glyph}</span>}
            <span className="install-option-copy">
                <strong lang={lang}>{title}</strong>
                {subtitle && <small id={describedBy} lang={lang && !capacity ? lang : undefined}>{subtitle}</small>}
                {capacity !== undefined && capacity !== null && (
                    <span className="install-capacity" aria-hidden="true">
                        <i style={{ width: `${Math.round(capacity * 100)}%` }} />
                        <i style={{ width: `${100 - Math.round(capacity * 100)}%` }} />
                    </span>
                )}
            </span>
            {tag && <span className="install-option-tag">{tag}</span>}
            <span className="install-check" aria-hidden="true"><CheckGlyph /></span>
        </button>
    );
};

export const MoreButton = ({ children, expanded, controls, onClick }) => (
    <button type="button" className="install-more" aria-expanded={expanded} aria-controls={controls} onClick={onClick}>
        {children}
    </button>
);

/* ── Switch rows ──────────────────────────────────────────────────────────── */
export const SwitchRow = ({ checked, dataFirst, disabled, id, onChange, subtitle, title }) => {
    const subtitleId = `${id}-subtitle`;
    return (
        <label className="install-switchrow" htmlFor={id} data-disabled={disabled ? "true" : undefined}>
            <span>
                <strong>{title}</strong>
                {subtitle && <small id={subtitleId}>{subtitle}</small>}
            </span>
            <input
              id={id}
              type="checkbox"
              role="switch"
              checked={!!checked}
              disabled={disabled}
              aria-checked={checked ? "true" : "false"}
              aria-describedby={subtitle ? subtitleId : undefined}
              data-first-control={dataFirst ? "true" : undefined}
              onChange={event => onChange?.(event.target.checked)}
            />
            <i aria-hidden="true" />
        </label>
    );
};

/* ── Fields ───────────────────────────────────────────────────────────────── */
const FieldMessages = ({ id, notes = [], problem }) => (
    <>
        {problem && (
            <p className="install-field-message" id={`${id}-problem`} aria-live="polite">
                <WarnGlyph />
                <span>{problem}</span>
            </p>
        )}
        {notes.map((note, index) => (
            <p className="install-field-message quiet" id={`${id}-note-${index}`} key={note}>{note}</p>
        ))}
    </>
);

const describedByFor = (id, problem, notes = []) => [
    problem ? `${id}-problem` : null,
    ...notes.map((_, index) => `${id}-note-${index}`),
].filter(Boolean).join(" ") || undefined;

export const TextField = ({ autoComplete, dataFirst, disabled, id, label, notes, onBlur, onChange, onEnter, problem, value, ...props }) => (
    <label className="install-field" htmlFor={id} data-invalid={problem ? "true" : undefined}>
        <span>{label}</span>
        <input
          id={id}
          type="text"
          value={value}
          disabled={disabled}
          autoComplete={autoComplete || "off"}
          spellCheck={false}
          aria-invalid={problem ? "true" : undefined}
          aria-describedby={describedByFor(id, problem, notes)}
          data-first-control={dataFirst ? "true" : undefined}
          onChange={event => onChange?.(event.target.value)}
          onBlur={onBlur}
          onKeyDown={event => {
              if (event.key === "Enter" && onEnter) {
                  event.preventDefault();
                  onEnter();
              }
          }}
          {...props}
        />
        <FieldMessages id={id} notes={notes} problem={problem} />
    </label>
);

export const SecretField = ({ dataFirst, disabled, id, label, noun = "password", notes, onChange, onEnter, problem, value }) => {
    const [shown, setShown] = useState(false);
    return (
        <div className="install-field" data-invalid={problem ? "true" : undefined}>
            <span id={`${id}-label`}>{label}</span>
            <span className="install-secret">
                <input
                  id={id}
                  type={shown ? "text" : "password"}
                  value={value}
                  disabled={disabled}
                  autoComplete="new-password"
                  spellCheck={false}
                  aria-labelledby={`${id}-label`}
                  aria-invalid={problem ? "true" : undefined}
                  aria-describedby={[describedByFor(id, problem, notes), `${id}-reveal-shortcut`].filter(Boolean).join(" ")}
                  data-first-control={dataFirst ? "true" : undefined}
                  onChange={event => onChange?.(event.target.value)}
                  onKeyDown={event => {
                      if (event.altKey && !event.ctrlKey && !event.metaKey && event.key.toLowerCase() === "r") {
                          event.preventDefault();
                          setShown(current => !current);
                          return;
                      }
                      if (event.key === "Enter" && onEnter) {
                          event.preventDefault();
                          onEnter();
                      }
                  }}
                />
                <button
                  type="button"
                  className="install-reveal"
                  aria-controls={id}
                  aria-pressed={shown ? "true" : "false"}
                  aria-label={`Show ${noun}`}
                  aria-keyshortcuts="Alt+R"
                  title={`Show or hide ${noun} (Alt+R)`}
                  tabIndex={-1}
                  disabled={disabled}
                  onClick={() => setShown(!shown)}
                >
                    <EyeGlyph open={!shown} />
                </button>
            </span>
            <VisuallyHidden id={`${id}-reveal-shortcut`}>Press Alt+R to show or hide this {noun}.</VisuallyHidden>
            <FieldMessages id={id} notes={notes} problem={problem} />
        </div>
    );
};

/**
 * A native select: keyboard, type-ahead and screen readers behave the way the
 * platform does. options: [{ value, label }] or groups [{ label, options }].
 */
export const SelectField = ({ dataFirst, disabled, groups, id, label, notes, onChange, options, problem, value }) => (
    <label className="install-field select" htmlFor={id} data-invalid={problem ? "true" : undefined}>
        <span>{label}</span>
        <select
          id={id}
          value={value}
          disabled={disabled}
          aria-invalid={problem ? "true" : undefined}
          aria-describedby={describedByFor(id, problem, notes)}
          data-first-control={dataFirst ? "true" : undefined}
          onChange={event => onChange?.(event.target.value)}
        >
            {options && options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
            {groups && groups.filter(group => group.options.length).map(group => (
                <optgroup key={group.label} label={group.label}>
                    {group.options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                </optgroup>
            ))}
        </select>
        <ChevronGlyph />
        <FieldMessages id={id} notes={notes} problem={problem} />
    </label>
);

export { DateTimeField } from "./DateTimeField.jsx";

export const Pair = ({ children }) => <div className="install-pair">{children}</div>;

/* ── Quiet lines, warnings, summary ───────────────────────────────────────── */
export const Note = ({ children, live }) => (
    <p className="install-note" aria-live={live ? "polite" : undefined}>{children}</p>
);

export const Warn = ({ children, id, live, plain, strong }) => (
    <div className="install-warn" id={id} role={live ? "status" : undefined}>
        <WarnGlyph />
        <span>
            {strong && <strong>{strong}</strong>}
            {strong && plain ? " " : null}
            {plain}
            {children}
        </span>
    </div>
);

/**
 * rows: [{ term, value, step, onEdit }]. Rows with onEdit are buttons that go
 * back to that step (handoff §2); the "Change" word appears on hover and focus
 * and is always part of the accessible name.
 */
export const Summary = ({ rows }) => (
    <dl className="install-summary">
        {rows.map(row => (
            <div key={row.term}>
                <dt id={`summary-${row.key}`}>{row.term}</dt>
                <dd>
                    {row.onEdit
                        ? (
                            <button
                              type="button"
                              className="install-summary-link"
                              aria-describedby={`summary-${row.key}`}
                              onClick={row.onEdit}
                            >
                                <span>{row.value}</span>
                                <span>Change<VisuallyHidden>{` ${row.term.toLowerCase()}`}</VisuallyHidden></span>
                            </button>
                        )
                        : row.value}
                </dd>
            </div>
        ))}
    </dl>
);

export const Meter = ({ label, value }) => (
    <div
      className="install-meter"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value === null ? undefined : value}
      aria-valuetext={value === null ? "In progress" : undefined}
      data-indeterminate={value === null ? "true" : undefined}
      style={value === null ? undefined : { "--ins-progress": `${value}%` }}
    >
        <i />
    </div>
);

export const Phases = ({ labels, states }) => (
    <ol className="install-phases">
        {labels.map((label, index) => (
            <li className="install-phase" data-state={states[index]} key={label}>
                <i aria-hidden="true" />
                {label}
                <VisuallyHidden>{`, ${{ doing: "in progress", done: "done", failed: "stopped", todo: "not started" }[states[index]]}`}</VisuallyHidden>
            </li>
        ))}
    </ol>
);

/* ── Dots ─────────────────────────────────────────────────────────────────── */
/**
 * Eight dots along the bottom. Finished steps are filled buttons that go back
 * to that step; the current step is filled; the rest are faint and not yet
 * reachable. Each dot's name is in a visually hidden label (never
 * display:none) and shows on hover and focus (handoff §6).
 */
export const Dots = ({ current, locked, names, onGo, reached }) => {
    const navId = useId();
    return (
        <nav aria-labelledby={navId}>
            <VisuallyHidden id={navId}>Installation steps</VisuallyHidden>
            <ol className="install-rail">
                {names.map((name, index) => {
                    const state = index === current ? "current" : index <= reached ? "done" : "todo";
                    const reachable = !locked && index !== current && index <= reached;
                    const stateText = state === "current" ? "current" : state === "done" ? "completed" : "not reached yet";
                    return (
                        <li key={name}>
                            <button
                              type="button"
                              data-state={state}
                              aria-current={state === "current" ? "step" : undefined}
                              aria-disabled={reachable ? undefined : "true"}
                              onClick={() => reachable && onGo(index)}
                            >
                                <b aria-hidden="true" />
                                <VisuallyHidden>{`Step ${index + 1}, ${name}, ${stateText}`}</VisuallyHidden>
                                <span className="install-rail-name" aria-hidden="true">{name}</span>
                            </button>
                        </li>
                    );
                })}
            </ol>
        </nav>
    );
};

export const Loading = ({ children }) => <p className="install-loading" role="status">{children}</p>;
