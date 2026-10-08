/* SPDX-License-Identifier: LGPL-2.1-or-later */
// Session persistence carries only the user's explicit non-secret choice.
// Never derive a choice from merely visiting the Lock step or restore secrets.
export const restoreEncryptionChoice = (state, answers) => {
    if (typeof answers?.lockChoice !== "boolean") {
        return state;
    }
    return { ...state, storage: { ...state.storage,
        luks: { ...state.storage.luks, encrypted: answers.lockChoice } } };
};
