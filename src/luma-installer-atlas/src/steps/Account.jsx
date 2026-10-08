/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 5 · Who is using this computer?
 * Anaconda's Users module owns the account. The first account is in wheel
 * (it can change system settings); root stays locked. Username guessing and
 * password crypting are anaconda-webui 68's (users.js, helpers/users.js).
 *
 * "Sign me in automatically" has no Anaconda property. It is recorded in the
 * installer's memory (/run/luma-atlas/autologin) and applied to the installed
 * system's display manager by the Luma installer kickstart's %post.
 */
import React, { useContext, useEffect, useRef, useState } from "react";

import { guessUsernameFromFullName } from "../apis/users.js";

import { setUsersAction } from "../actions/users-actions.js";

import { error as logError } from "../helpers/log.js";
import { applyAccounts } from "../helpers/users.js";
import { fullNameProblem, guessUsername, secretProblems, usernameProblem } from "../model/account.js";

import { ProbeContext, RuntimeContext, UsersContext } from "../contexts/Common.jsx";

import { Actions, Button, Pair, SecretField, Spacer, Step, SwitchRow, TextField, Warn } from "../atlas/components.jsx";
import { setAutologinIntent } from "../system/host.js";
import { usePasswordQuality } from "./Lock.jsx";

export const AccountStep = ({ answers, back, continueRef, dispatch, index, invalidateAfter, next, panelRef, setAnswers }) => {
    const accounts = useContext(UsersContext);
    const probe = useContext(ProbeContext);
    const policy = useContext(RuntimeContext).passwordPolicies?.user;
    const [usernameEdited, setUsernameEdited] = useState(!!accounts.userName);
    const [touched, setTouched] = useState({});
    const [busy, setBusy] = useState(false);
    const [failure, setFailure] = useState(null);
    const guessSeq = useRef(0);

    const set = (change) => {
        invalidateAfter(index);
        dispatch(setUsersAction({ isRootEnabled: false, skipAccountCreation: false, ...change }));
    };

    // The username follows the name until the person edits it.
    useEffect(() => {
        if (usernameEdited) {
            return;
        }
        const seq = ++guessSeq.current;
        const timer = setTimeout(async () => {
            let guess = guessUsername(accounts.fullName);
            if (accounts.fullName.trim()) {
                try {
                    guess = await guessUsernameFromFullName(accounts.fullName);
                } catch (exception) {
                    logError("atlas: username guess failed", exception?.message);
                }
            }
            if (seq === guessSeq.current) {
                dispatch(setUsersAction({ userName: (guess || "").toLowerCase() }));
            }
        }, 200);
        return () => clearTimeout(timer);
    }, [accounts.fullName, dispatch, usernameEdited]);

    const taken = probe?.payload?.systemUsers || [];
    const nameProblem = fullNameProblem(accounts.fullName);
    const userProblem = usernameProblem(accounts.userName, { taken });
    const quality = usePasswordQuality(accounts.password);
    const secret = secretProblems({ confirm: accounts.confirmPassword, noun: "password", policy, quality, secret: accounts.password });

    const valid = !busy && !!accounts.userName && !userProblem && !nameProblem && secret.valid;

    const onContinue = async () => {
        if (!valid) {
            setTouched({ confirm: true, password: true, userName: true });
            return;
        }
        setBusy(true);
        setFailure(null);
        try {
            await applyAccounts({ ...accounts, isRootEnabled: false, skipAccountCreation: false });
            await setAutologinIntent(answers.autologin ? accounts.userName : null);
            next();
        } catch (exception) {
            logError("atlas: saving the account failed", exception?.message);
            setFailure("Luma could not save this account. Try again.");
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
                  <Button variant="primary" arrow busy={busy} disabled={!valid} onClick={onContinue}>Continue</Button>
              </Actions>
          )}
          id="account"
          index={index}
          lede="Your account can change system settings."
          panelRef={panelRef}
          title="Who is using this computer?"
        >
            <Pair>
                <TextField
                  autoComplete="name"
                  dataFirst
                  id="account-name"
                  label="Your name"
                  onChange={fullName => set({ fullName })}
                  problem={nameProblem}
                  value={accounts.fullName}
                />
                <TextField
                  autoComplete="username"
                  id="account-username"
                  label="Username"
                  onBlur={() => setTouched(current => ({ ...current, userName: true }))}
                  onChange={userName => {
                      setUsernameEdited(true);
                      set({ userName });
                  }}
                  problem={userProblem || (touched.userName && !accounts.userName ? "Choose a username." : null)}
                  value={accounts.userName}
                />
            </Pair>
            <Pair>
                <SecretField
                  id="account-password"
                  label="Password"
                  notes={secret.notes}
                  onChange={password => set({ password })}
                  problem={secret.secretProblem || (touched.password && !accounts.password ? "Choose a password." : null)}
                  value={accounts.password}
                />
                <SecretField
                  id="account-confirm"
                  label="Type it again"
                  onChange={confirmPassword => set({ confirmPassword })}
                  onEnter={onContinue}
                  problem={secret.confirmProblem}
                  value={accounts.confirmPassword}
                />
            </Pair>
            <SwitchRow
              checked={answers.autologin}
              id="account-autologin"
              onChange={autologin => {
                  invalidateAfter(index);
                  setAnswers(current => ({ ...current, autologin }));
              }}
              subtitle="Skips the login screen. Turn this off if other people use the computer"
              title="Sign me in automatically"
            />
            {failure && <Warn live strong={failure} />}
        </Step>
    );
};
