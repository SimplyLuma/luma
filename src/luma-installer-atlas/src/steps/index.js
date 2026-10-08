/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Fedora 44's step order, one for one, in Luma's words (handoff §1), with
 * "Get more apps" (ADR-028 Depot, section 14) before the review so the review
 * still shows every answer.
 */
import { AccountStep } from "./Account.jsx";
import { AppsStep } from "./Apps.jsx";
import { DestinationStep } from "./Destination.jsx";
import { FinishedStep } from "./Finished.jsx";
import { InstallingStep } from "./Installing.jsx";
import { LanguageStep } from "./Language.jsx";
import { LockStep } from "./Lock.jsx";
import { ReviewStep } from "./Review.jsx";
import { TimeStep } from "./Time.jsx";

export const STEPS = [
    { component: LanguageStep, id: "language", name: "Language", title: "What language should Luma speak?" },
    { component: TimeStep, id: "location", name: "Date and time", title: "Where in the world are you?" },
    { component: DestinationStep, id: "disk", name: "Where to install", title: "Where should Luma live?" },
    { component: LockStep, id: "encryption", name: "Lock the drive", title: "Should we lock this drive?" },
    { component: AccountStep, id: "account", name: "Your account", title: "Who is using this computer?" },
    { component: AppsStep, id: "apps", name: "More apps", title: "Would you like more apps?" },
    { component: ReviewStep, id: "review", name: "Review", title: "Here is what will happen." },
    { component: InstallingStep, id: "installing", name: "Installing", title: "Setting up Luma." },
    { component: FinishedStep, id: "done", name: "Finished", title: "Luma is ready." },
];
