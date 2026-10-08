/* SPDX-License-Identifier: LGPL-2.1-or-later */

// Anaconda44's Finish consumes the saved error. The stopped signal and the
// resume/property reply must observe the SAME completion, including rejection.
export const taskCompletion = (task) => {
    let completion;
    return () => {
        completion ||= Promise.resolve().then(() => task.Finish());
        return completion;
    };
};
