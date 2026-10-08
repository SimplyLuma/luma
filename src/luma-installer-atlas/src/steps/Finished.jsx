/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 * Reached only after the installer medium has been ejected.
 */
import React from "react";
import { exitGui } from "../helpers/exit.js";
import { finishedLede } from "../model/delivery.js";
import { Actions, Button, Step } from "../atlas/components.jsx";

export const FinishedStep = ({ continueRef, index, panelRef }) => {
    continueRef.current = null;
    return (
        <Step
          actions={(
              <Actions>
                  <Button variant="primary" data-first-control="true" onClick={exitGui}>Restart now</Button>
              </Actions>
          )}
          id="done"
          index={index}
          lede={finishedLede()}
          panelRef={panelRef}
          title="Luma is ready."
        />
    );
};
