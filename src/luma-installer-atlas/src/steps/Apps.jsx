/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 6 · Would you like more apps?
 * ADR-028 (Depot) section 14: collections from the seed catalog, no account,
 * nothing preselected. The installer downloads nothing; the choice is written
 * to /etc/luma/first-boot-apps.json on the installed system by the installer
 * kickstart, and Depot's first-boot provisioning installs from the Luma
 * remote. Not in the install-wizard design; drawn with its components.
 */
import React, { useMemo } from "react";

import catalog from "../data/app-collections.json";
import { error as logError } from "../helpers/log.js";
import { collectionRows, firstBootApps, selectionNote, toggleCollection } from "../model/apps.js";

import { Actions, Button, Note, Option, Options, Spacer, Step, Warn } from "../atlas/components.jsx";
import { setFirstBootAppsIntent } from "../system/host.js";

export const AppsStep = ({ answers, back, continueRef, index, next, panelRef, setAnswers }) => {
    const rows = useMemo(() => collectionRows(catalog), []);
    const selected = answers.collections || [];
    const [failure, setFailure] = React.useState(null);
    const [busy, setBusy] = React.useState(false);
    const note = selectionNote(catalog, selected);

    const onContinue = async () => {
        if (busy) {
            return;
        }
        setBusy(true);
        setFailure(null);
        try {
            await setFirstBootAppsIntent(firstBootApps(catalog, selected));
            next();
        } catch (exception) {
            logError("atlas: recording first-boot apps failed", exception?.message);
            setFailure("Luma could not save this choice. Try again.");
        } finally {
            setBusy(false);
        }
    };
    continueRef.current = onContinue;

    return (
        <Step
          actions={(
              <Actions>
                  <Button variant="quiet" onClick={back}>Back</Button>
                  <Spacer />
                  <Button variant="primary" arrow busy={busy} onClick={onContinue}>Continue</Button>
              </Actions>
          )}
          id="apps"
          index={index}
          lede="Optional apps download after setup. Internet is required."
          panelRef={panelRef}
          title="Would you like more apps?"
        >
            <Options labelledBy="apps-title">
                {rows.map((row, position) => (
                    <Option
                      dataFirst={position === 0}
                      id={`apps-${row.id}`}
                      key={row.id}
                      onSelect={() => setAnswers(current => ({ ...current, collections: toggleCollection(current.collections || [], row.id) }))}
                      pressed={selected.includes(row.id)}
                      subtitle={row.subtitle}
                      title={row.name}
                    />
                ))}
            </Options>
            {note && <Note live>{note}</Note>}
            <Note>You can always download these from the Depot at a later time.</Note>
            {failure && <Warn live strong={failure} />}
        </Step>
    );
};
