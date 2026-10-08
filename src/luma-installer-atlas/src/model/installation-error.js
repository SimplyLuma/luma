/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Task.Finish rejects with Anaconda's original exception, delivered by
 * Cockpit as exception.message. Display that message, never a full log,
 * traceback, exception.stack, or guessed explanation of what failed.
 */
export const missingInstallationError = "Anaconda did not return an error message. Save the log for more information.";

export const installationErrorDetails = (exception) => {
    if (typeof exception?.message !== "string" || !exception.message.trim()) {
        return missingInstallationError;
    }
    let message = exception.message
            .replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "")
            .replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/g, " ");
    // A command's stderr may include a traceback. The authoritative leading
    // error stays useful; its stack frames belong in the saved diagnostic.
    message = message.split("Traceback (most recent call last):")[0].trim();
    if (!message) {
        return "Anaconda returned an error with a traceback. Save the log for the complete details.";
    }
    message = message
            .replace(/(\b[a-z][a-z0-9+.-]*:\/\/)[^\s/@]+(?::[^\s/@]*)?@/gi, "$1[redacted]@")
            .replace(/([?&](?:password|passwd|passphrase|token|secret|credential|access_token|api_key|signature)=)[^&\s"']*/gi, "$1[redacted]")
            .replace(/(\bauthorization\s*:\s*)(?:bearer|basic)\s+[^\s,;]+/gi, "$1[redacted]")
            .replace(/((?:--)?\b(?:password|passwd|passphrase|token|secret|credential|access_token|api_key)\b["']?\s*(?:[:=]\s*|\s+))(?:"[^"]*"|'[^']*'|[^\s,;&]+)/gi, "$1[redacted]")
            .replace(/\s+/g, " ");
    return message.length > 2000 ? `${message.slice(0, 2000)}…` : message;
};
