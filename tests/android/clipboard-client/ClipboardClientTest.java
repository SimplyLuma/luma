// SPDX-License-Identifier: Apache-2.0
// Executes the production SDK wrapper with transport/dependency fixtures.
import android.content.Context;
import android.os.IBinder;
import android.os.RemoteException;
import android.os.ServiceManager;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;
import lineageos.waydroid.Clipboard;
import lineageos.waydroid.IClipboard;

public final class ClipboardClientTest {
    private static final class Endpoint implements IClipboard, IBinder {
        volatile boolean alive = true;
        boolean readFails, writeFails;
        String text;
        int writes;
        CountDownLatch entered, release;
        Endpoint(String value) { text = value; }
        public IBinder asBinder() { return this; }
        public boolean isBinderAlive() { return alive; }
        public void sendClipboardData(String value) throws RemoteException {
            writes++;
            if (writeFails) throw new RemoteException();
            text = value;
        }
        public String getClipboardData() throws RemoteException {
            if (entered != null) {
                entered.countDown();
                try { release.await(); }
                catch (InterruptedException error) { throw new AssertionError(error); }
            }
            if (readFails) throw new RemoteException();
            return text;
        }
    }
    private static void expect(boolean value) {
        if (!value) throw new AssertionError("Clipboard client contract failed");
    }
    public static void main(String[] args) throws Exception {
        Clipboard client = Clipboard.getInstance(new Context());
        expect("".equals(client.getClipboardData()));
        client.sendClipboardData("not delivered before service registration");
        Endpoint first = new Endpoint("first");
        ServiceManager.endpoint = first;
        expect("first".equals(client.getClipboardData()));
        client.sendClipboardData("written");
        expect(first.writes == 1 && "written".equals(first.text));

        first.alive = false;
        Endpoint second = new Endpoint("second");
        ServiceManager.endpoint = second;
        expect("second".equals(client.getClipboardData()));
        second.readFails = true;
        expect("".equals(client.getClipboardData()));
        Endpoint third = new Endpoint("third");
        ServiceManager.endpoint = third;
        expect("third".equals(client.getClipboardData()));
        third.writeFails = true;
        client.sendClipboardData("failed write");
        expect(third.writes == 1); // Never replay a failed transaction blindly.
        Endpoint fourth = new Endpoint(null);
        ServiceManager.endpoint = fourth;
        expect("".equals(client.getClipboardData()));
        client.sendClipboardData("after restart");
        expect(fourth.writes == 1 && "after restart".equals(fourth.text));

        // A late failure from an old proxy must not discard a new connection.
        fourth.entered = new CountDownLatch(1);
        fourth.release = new CountDownLatch(1);
        fourth.readFails = true;
        Thread oldRead = new Thread(() -> expect("".equals(client.getClipboardData())));
        AtomicReference<Throwable> threadFailure = new AtomicReference<>();
        oldRead.setUncaughtExceptionHandler((thread, error) -> threadFailure.set(error));
        oldRead.start();
        fourth.entered.await();
        fourth.alive = false;
        Endpoint fifth = new Endpoint("current");
        ServiceManager.endpoint = fifth;
        expect("current".equals(client.getClipboardData()));
        int lookups = ServiceManager.lookups;
        fourth.release.countDown();
        oldRead.join(2000);
        expect(!oldRead.isAlive() && threadFailure.get() == null);
        expect("current".equals(client.getClipboardData()));
        expect(ServiceManager.lookups == lookups);
        System.out.println("production Clipboard: registration, round trip, death, read/write failure and concurrent reconnect passed");
    }
}
