// SPDX-License-Identifier: Apache-2.0
package org.projectluma.androidproviderprobe;

import android.app.Instrumentation;
import android.accounts.AccountManager;
import android.accounts.AuthenticatorDescription;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.IBinder;
import android.os.Process;
import java.net.URL;
import java.security.MessageDigest;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONObject;

/** Ordinary-UID network and provider visibility probe; never requests accounts or tokens. */
public final class ProviderProbe extends Instrumentation {
    private static final String COMPATIBLE_SIGNER =
            "f0fd6c5b410f25cb25c3b53346c8972fae30f8ee7411df910480ad6b2d60db83";
    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        start();
    }

    @Override public void onStart() {
        JSONObject result = new JSONObject();
        boolean passed = false;
        try {
            Context context = getTargetContext();
            PackageManager pm = context.getPackageManager();
            result.put("uid", Process.myUid());
            result.put("gms", provider(pm, "com.google.android.gms"));
            result.put("companion", provider(pm, "com.android.vending"));
            boolean googleAuthenticator = false;
            for (AuthenticatorDescription authenticator : AccountManager.get(context).getAuthenticatorTypes()) {
                if ("com.google".equals(authenticator.type)
                        && "com.google.android.gms".equals(authenticator.packageName)) {
                    googleAuthenticator = true;
                }
            }
            result.put("google_authenticator", googleAuthenticator);
            Intent signIn = new Intent("com.google.android.gms.auth.api.signin.service.START")
                    .setPackage("com.google.android.gms");
            result.put("signin_service_resolves", pm.resolveService(signIn, 0) != null);
            CountDownLatch connected = new CountDownLatch(1);
            ServiceConnection connection = new ServiceConnection() {
                @Override public void onServiceConnected(ComponentName name, IBinder binder) {
                    if (binder != null && binder.isBinderAlive()) connected.countDown();
                }
                @Override public void onServiceDisconnected(ComponentName name) {}
            };
            boolean bound = context.bindService(signIn, connection, Context.BIND_AUTO_CREATE);
            try {
                result.put("signin_service_bound", bound && connected.await(5, TimeUnit.SECONDS));
            } finally {
                if (bound) context.unbindService(connection);
            }
            HttpsURLConnection https = (HttpsURLConnection)
                    new URL("https://www.google.com/generate_204").openConnection();
            try {
                https.setConnectTimeout(5000);
                https.setReadTimeout(5000);
                https.setInstanceFollowRedirects(false);
                result.put("https_status", https.getResponseCode());
                result.put("tls_cipher", https.getCipherSuite());
            } finally {
                https.disconnect();
            }
            passed = Process.myUid() >= 10000 && googleAuthenticator
                    && result.getBoolean("signin_service_resolves")
                    && result.getBoolean("signin_service_bound")
                    && result.getInt("https_status") == 204;
        } catch (Exception error) {
            try { result.put("error", error.toString()); }
            catch (Exception ignored) {}
        }
        try {
            result.put("passed", passed);
            result.put("account_login_tested", false);
        } catch (Exception ignored) {}
        Bundle report = new Bundle();
        report.putString("provider_probe", result.toString());
        finish(passed ? -1 : 0, report);
    }

    @SuppressWarnings("deprecation")
    private JSONObject provider(PackageManager pm, String name) throws Exception {
        PackageInfo info = pm.getPackageInfo(name, PackageManager.GET_SIGNATURES);
        if (info.signatures == null || info.signatures.length != 1)
            throw new IllegalStateException("Expected one visible provider certificate: " + name);
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(info.signatures[0].toByteArray());
        StringBuilder hex = new StringBuilder();
        for (byte value : digest) hex.append(String.format("%02x", value & 255));
        if (!COMPATIBLE_SIGNER.equals(hex.toString()))
            throw new IllegalStateException("Provider compatibility identity unavailable: " + name);
        JSONObject result = new JSONObject();
        result.put("version", info.versionName);
        result.put("visible_signer_sha256", hex.toString());
        return result;
    }
}
