package dev.mirror.repurpose.health;

import android.os.Binder;
import android.os.IBinder;
import android.os.Parcel;
import android.os.Process;
import android.os.RemoteException;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/**
 * What passes between Mirror Home and an OTA supervisor that it holds on to:
 * how readily the kernel would end the supervisor's process, and a request to
 * power the display off when the supervisor is device owner.
 *
 * <p>The kernel keeps that number for every process ("oom_score_adj": 0 for
 * what is on the display, 58 for what that needs, 294 for a background
 * service, more for what is merely kept around) and ends those with the
 * highest first. Android 6 lets a process read only its own, so the
 * supervisor reads it and Mirror Home asks: that is how the hold can be
 * seen to work from another room.
 */
public final class HoldLink {
    /** Where the kernel says nothing. */
    public static final int UNKNOWN = Integer.MIN_VALUE;

    /** Turns the display off on Mirror Home's behalf when the supervisor is device owner. */
    public interface DisplaySleeper {
        boolean sleepDisplay();
    }

    private static final String DESCRIPTOR = "dev.mirror.repurpose.updater.IHold";
    private static final int DESCRIBE = IBinder.FIRST_CALL_TRANSACTION;
    private static final int SLEEP_DISPLAY = IBinder.FIRST_CALL_TRANSACTION + 1;

    private HoldLink() {
    }

    /** The supervisor's end: answers with its process and that process's number. */
    public static IBinder serve() {
        return serve(null);
    }

    /** The supervisor's end, also able to turn the display off on request. */
    public static IBinder serve(final DisplaySleeper sleeper) {
        return new Binder() {
            @Override
            protected boolean onTransact(int code, Parcel data, Parcel reply, int flags)
                    throws RemoteException {
                if (code == DESCRIBE) {
                    data.enforceInterface(DESCRIPTOR);
                    reply.writeNoException();
                    reply.writeInt(Process.myPid());
                    reply.writeInt(score(new File("/proc/self/oom_score_adj")));
                    return true;
                }
                if (code == SLEEP_DISPLAY) {
                    data.enforceInterface(DESCRIPTOR);
                    boolean slept = sleeper != null && sleeper.sleepDisplay();
                    reply.writeNoException();
                    reply.writeInt(slept ? 1 : 0);
                    return true;
                }
                return super.onTransact(code, data, reply, flags);
            }
        };
    }

    /**
     * Mirror Home's end: asks the supervisor to power the display off now,
     * driving the hardware backlight to true zero. False if unsupported or refused.
     */
    public static boolean sleepDisplay(IBinder link) {
        Parcel data = Parcel.obtain();
        Parcel reply = Parcel.obtain();
        try {
            data.writeInterfaceToken(DESCRIPTOR);
            if (!link.transact(SLEEP_DISPLAY, data, reply, 0)) {
                return false;
            }
            reply.readException();
            return reply.readInt() != 0;
        } catch (RemoteException | RuntimeException unsupported) {
            return false;
        } finally {
            reply.recycle();
            data.recycle();
        }
    }

    /**
     * Mirror Home's end: the supervisor's process and its number, or null if
     * the supervisor did not answer. The number is {@link #UNKNOWN} where the
     * kernel says nothing.
     */
    public static int[] describe(IBinder link) {
        Parcel data = Parcel.obtain();
        Parcel reply = Parcel.obtain();
        try {
            data.writeInterfaceToken(DESCRIPTOR);
            if (!link.transact(DESCRIBE, data, reply, 0)) {
                return null;
            }
            reply.readException();
            return new int[]{reply.readInt(), reply.readInt()};
        } catch (RemoteException | RuntimeException gone) {
            return null;
        } finally {
            reply.recycle();
            data.recycle();
        }
    }

    /** The number in a file such as /proc/self/oom_score_adj; {@link #UNKNOWN} if there is none. */
    public static int score(File file) {
        try (InputStream in = new FileInputStream(file)) {
            byte[] buffer = new byte[32];
            int length = Math.max(0, in.read(buffer));
            return Integer.parseInt(new String(buffer, 0, length, StandardCharsets.UTF_8).trim());
        } catch (IOException | RuntimeException unreadable) {
            return UNKNOWN;
        }
    }
}
