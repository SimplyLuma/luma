/*
 * Copyright (C) 2021 Red Hat, Inc.
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */
import cockpit from "cockpit";

import React from "react";
import { createRoot } from "react-dom/client";

import { convertToCockpitLang } from "./helpers/language.js";

import { Application } from "./app.jsx";

import "./atlas/atlas.css";

document.addEventListener("DOMContentLoaded", () => {
    document.documentElement.setAttribute("dir", cockpit.language_direction || "ltr");
    document.documentElement.setAttribute("lang", convertToCockpitLang({ lang: cockpit.language || "en" }));
    createRoot(document.getElementById("app")).render(<Application />);
});
