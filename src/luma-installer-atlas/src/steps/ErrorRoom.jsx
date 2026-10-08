/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * When the installer itself fails (the backend stops, a module cannot be
 * read), the same room says so plainly and offers the log and a restart.
 * Replaces anaconda-webui 68's PatternFly error dialog and bug-report flow.
 */
import React, { useState } from "react";

import { exitGui } from "../helpers/exit.js";

import { Actions, Button, Note, Spacer, Warn } from "../atlas/components.jsx";
import { saveLogs } from "../system/host.js";

export const ErrorRoom = ({ exception }) => {
    const [logs, setLogs] = useState(null);
    const detail = [exception?.context, exception?.message].filter(Boolean).join(" ");

    const onSave = async () => {
        setLogs({ saving: true });
        try {
            const result = await saveLogs([]);
            setLogs({ text: result.ok ? `Saved to ${result.label} as ${result.file}.` : "Plug in a USB drive to save the log to, then try again." });
        } catch (error) {
            setLogs({ text: "The log could not be saved. Plug in a USB drive, then try again." });
        }
    };

    return (
        <div className="install-body">
            <div className="install-panels">
                <section className="install-panel" aria-labelledby="error-title" data-step="error">
                    <h1 className="install-title" id="error-title">Something went wrong.</h1>
                    <p className="install-lede">
                        The installer stopped unexpectedly. Save the log so someone can see what happened, then restart.
                    </p>
                    {detail && <Warn plain={detail} />}
                    {logs?.text && <Note live>{logs.text}</Note>}
                    <Actions>
                        <Button variant="quiet" busy={logs?.saving} onClick={onSave}>Save the log</Button>
                        <Spacer />
                        <Button variant="primary" onClick={exitGui}>Restart</Button>
                    </Actions>
                </section>
            </div>
        </div>
    );
};
