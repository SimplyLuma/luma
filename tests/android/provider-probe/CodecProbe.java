// SPDX-License-Identifier: Apache-2.0
package org.projectluma.androidproviderprobe;

import android.app.Instrumentation;
import android.media.MediaCodec;
import android.media.MediaFormat;
import android.os.Bundle;
import android.os.Process;
import android.os.SystemClock;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

/** Exercise the OMX allocation path seen in the game's native crash, without accounts or media. */
public final class CodecProbe extends Instrumentation {
    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        start();
    }

    @Override public void onStart() {
        JSONObject result = new JSONObject();
        ExecutorService workers = Executors.newFixedThreadPool(4);
        CountDownLatch start = new CountDownLatch(1);
        boolean passed = false;
        long began = SystemClock.elapsedRealtime();
        try {
            List<Future<Integer>> pending = new ArrayList<>();
            for (int thread = 0; thread < 4; thread++) {
                pending.add(workers.submit(() -> {
                    start.await();
                    int completed = 0;
                    for (int iteration = 0; iteration < 25; iteration++) {
                        if (Thread.currentThread().isInterrupted()) break;
                        MediaCodec codec = MediaCodec.createByCodecName("OMX.google.h264.decoder");
                        try {
                            MediaFormat format = MediaFormat.createVideoFormat("video/avc", 64, 64);
                            codec.configure(format, null, null, 0);
                            codec.start();
                            codec.stop();
                            completed++;
                        } finally {
                            codec.release();
                        }
                    }
                    return completed;
                }));
            }
            start.countDown();
            int completed = 0;
            long deadline = began + 20000;
            for (Future<Integer> task : pending) {
                completed += task.get(Math.max(1, deadline - SystemClock.elapsedRealtime()),
                                      TimeUnit.MILLISECONDS);
            }
            result.put("completed", completed);
            passed = completed == 100 && Process.myUid() >= 10000;
        } catch (Exception error) {
            try { result.put("error", error.toString()); }
            catch (Exception ignored) {}
        } finally {
            workers.shutdownNow();
        }
        try {
            result.put("uid", Process.myUid());
            result.put("codec", "OMX.google.h264.decoder");
            result.put("elapsed_ms", SystemClock.elapsedRealtime() - began);
            result.put("passed", passed);
            result.put("decoded_frames_tested", false);
        } catch (Exception ignored) {}
        Bundle report = new Bundle();
        report.putString("codec_probe", result.toString());
        finish(passed ? -1 : 0, report);
    }
}
