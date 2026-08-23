package dev.mirror.repurpose;

import android.content.Context;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

public final class FCastServer {
    public static final int PORT = 46899;

    private static final String TAG = "FCastServer";
    private static final String SERVICE_TYPE = "_fcast._tcp.";
    private static final int MAX_PACKET_BYTES = 32_000;
    private static final int MAX_CLIENTS = 8;
    private static final int CLIENT_IDLE_TIMEOUT_MS = 5 * 60 * 1000;

    private static final int OPCODE_PLAY = 1;
    private static final int OPCODE_PAUSE = 2;
    private static final int OPCODE_RESUME = 3;
    private static final int OPCODE_STOP = 4;
    private static final int OPCODE_SEEK = 5;
    private static final int OPCODE_PLAYBACK_UPDATE = 6;
    private static final int OPCODE_VOLUME_UPDATE = 7;
    private static final int OPCODE_SET_VOLUME = 8;
    private static final int OPCODE_PLAYBACK_ERROR = 9;
    private static final int OPCODE_SET_SPEED = 10;
    private static final int OPCODE_VERSION = 11;
    private static final int OPCODE_PING = 12;
    private static final int OPCODE_PONG = 13;
    private static final int OPCODE_INITIAL = 14;

    private final Context context;
    private final MediaPlaybackManager media;
    private final ConfigStore configStore;
    private final ExecutorService clients = Executors.newCachedThreadPool();
    private final Set<ClientConnection> connections =
            Collections.newSetFromMap(new ConcurrentHashMap<ClientConnection, Boolean>());
    private final AtomicInteger activeClients = new AtomicInteger();

    private volatile boolean running;
    private ServerSocket serverSocket;
    private Thread acceptThread;
    private Thread updateThread;
    private NsdManager.RegistrationListener registrationListener;

    public FCastServer(
            Context context,
            MediaPlaybackManager media,
            ConfigStore configStore) {
        this.context = context.getApplicationContext();
        this.media = media;
        this.configStore = configStore;
    }

    public synchronized void start() throws IOException {
        if (running) {
            return;
        }
        serverSocket = new ServerSocket(PORT);
        serverSocket.setReuseAddress(true);
        running = true;

        acceptThread = new Thread(this::acceptLoop, "fcast-accept");
        acceptThread.start();
        updateThread = new Thread(this::updateLoop, "fcast-updates");
        updateThread.start();
        registerService();
        Log.i(TAG, "FCast v3 receiver listening on port " + PORT);
    }

    public synchronized void stop() {
        running = false;
        unregisterService();
        if (serverSocket != null) {
            try {
                serverSocket.close();
            } catch (IOException ignored) {
                // Closing the server socket is best effort during shutdown.
            }
            serverSocket = null;
        }
        for (ClientConnection connection : connections) {
            connection.close();
        }
        connections.clear();
        clients.shutdownNow();
    }

    private void acceptLoop() {
        while (running) {
            try {
                Socket socket = serverSocket.accept();
                socket.setTcpNoDelay(true);
                socket.setKeepAlive(true);
                socket.setSoTimeout(CLIENT_IDLE_TIMEOUT_MS);
                if (activeClients.incrementAndGet() > MAX_CLIENTS) {
                    activeClients.decrementAndGet();
                    socket.close();
                    continue;
                }
                try {
                    ClientConnection connection = new ClientConnection(socket);
                    clients.execute(connection);
                } catch (IOException | RuntimeException error) {
                    activeClients.decrementAndGet();
                    socket.close();
                    Log.w(TAG, "Unable to initialize FCast client", error);
                }
            } catch (SocketException error) {
                if (running) {
                    Log.e(TAG, "FCast accept failed", error);
                }
            } catch (IOException error) {
                Log.e(TAG, "FCast accept failed", error);
            }
        }
    }

    private void updateLoop() {
        while (running) {
            try {
                Thread.sleep(1000);
                JSONObject snapshot = media.snapshot();
                JSONObject update = new JSONObject();
                update.put("generationTime", System.currentTimeMillis());
                update.put("state", playbackState(snapshot.optString("state")));
                update.put("time", snapshot.optDouble("positionSeconds", 0));
                if (!snapshot.isNull("durationSeconds")) {
                    update.put("duration", snapshot.optDouble("durationSeconds"));
                }
                update.put("speed", snapshot.optDouble("speed", 1));
                broadcast(OPCODE_PLAYBACK_UPDATE, update);

                JSONObject volume = new JSONObject();
                volume.put("generationTime", System.currentTimeMillis());
                volume.put("volume", snapshot.optDouble("volume", 1));
                broadcast(OPCODE_VOLUME_UPDATE, volume);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                return;
            } catch (JSONException error) {
                Log.e(TAG, "Unable to build FCast update", error);
            }
        }
    }

    private void broadcast(int opcode, JSONObject body) {
        for (ClientConnection connection : connections) {
            try {
                connection.send(opcode, body);
            } catch (IOException error) {
                connection.close();
                connections.remove(connection);
            }
        }
    }

    private void registerService() {
        NsdManager manager = (NsdManager) context.getSystemService(Context.NSD_SERVICE);
        if (manager == null) {
            return;
        }
        NsdServiceInfo info = new NsdServiceInfo();
        info.setServiceName(configStore.getDisplayName());
        info.setServiceType(SERVICE_TYPE);
        info.setPort(PORT);
        info.setAttribute("v", "3");

        registrationListener = new NsdManager.RegistrationListener() {
            @Override
            public void onServiceRegistered(NsdServiceInfo serviceInfo) {
                Log.i(TAG, "FCast advertised as " + serviceInfo.getServiceName());
            }

            @Override
            public void onRegistrationFailed(NsdServiceInfo serviceInfo, int errorCode) {
                Log.w(TAG, "FCast advertisement failed: " + errorCode);
            }

            @Override
            public void onServiceUnregistered(NsdServiceInfo serviceInfo) {
            }

            @Override
            public void onUnregistrationFailed(NsdServiceInfo serviceInfo, int errorCode) {
                Log.w(TAG, "FCast unregistration failed: " + errorCode);
            }
        };
        try {
            manager.registerService(info, NsdManager.PROTOCOL_DNS_SD, registrationListener);
        } catch (RuntimeException error) {
            registrationListener = null;
            Log.w(TAG, "FCast advertisement could not start", error);
        }
    }

    private void unregisterService() {
        if (registrationListener == null) {
            return;
        }
        NsdManager manager = (NsdManager) context.getSystemService(Context.NSD_SERVICE);
        if (manager != null) {
            try {
                manager.unregisterService(registrationListener);
            } catch (IllegalArgumentException ignored) {
                // Registration may have failed before shutdown.
            }
        }
        registrationListener = null;
    }

    private final class ClientConnection implements Runnable {
        private final Socket socket;
        private final BufferedInputStream input;
        private final BufferedOutputStream output;

        ClientConnection(Socket socket) throws IOException {
            this.socket = socket;
            input = new BufferedInputStream(socket.getInputStream());
            output = new BufferedOutputStream(socket.getOutputStream());
        }

        @Override
        public void run() {
            try {
                send(OPCODE_VERSION, new JSONObject().put("version", 3));
                send(
                        OPCODE_INITIAL,
                        new JSONObject()
                                .put("displayName", configStore.getDisplayName())
                                .put("appName", "Mirror Repurpose")
                                .put("appVersion", BuildConfig.VERSION_NAME)
                                .put("playData", JSONObject.NULL));
                connections.add(this);
                while (running && !socket.isClosed()) {
                    int size = readLittleEndianInt();
                    if (size < 1 || size > MAX_PACKET_BYTES) {
                        throw new IOException("Invalid FCast packet size " + size);
                    }
                    int opcode = input.read();
                    if (opcode < 0) {
                        throw new EOFException();
                    }
                    byte[] body = readExactly(size - 1);
                    try {
                        handle(opcode, body);
                    } catch (JSONException | ProtocolException error) {
                        send(
                                OPCODE_PLAYBACK_ERROR,
                                new JSONObject().put("message", error.getMessage()));
                    }
                }
            } catch (EOFException ignored) {
                // Normal sender disconnect.
            } catch (Exception error) {
                if (running) {
                    Log.w(TAG, "FCast client failed", error);
                    try {
                        send(
                                OPCODE_PLAYBACK_ERROR,
                                new JSONObject().put("message", error.getMessage()));
                    } catch (Exception ignored) {
                        // The connection is already unusable.
                    }
                }
            } finally {
                close();
                connections.remove(this);
                activeClients.decrementAndGet();
            }
        }

        private void handle(int opcode, byte[] body)
                throws JSONException, ProtocolException, IOException {
            JSONObject message = body.length == 0
                    ? new JSONObject()
                    : new JSONObject(new String(body, StandardCharsets.UTF_8));
            switch (opcode) {
                case OPCODE_PLAY:
                    String url = message.optString("url", null);
                    MediaPlaybackManager.PlayRequest request =
                            new MediaPlaybackManager.PlayRequest(
                                    url,
                                    message.optString("container", null),
                                    message.optJSONObject("metadata") == null
                                            ? null
                                            : message.optJSONObject("metadata").optString("title", null),
                                    ControlServer.parseRequestHeaders(
                                            message.optJSONObject("headers")),
                                    message.optDouble("time", 0),
                                    message.has("volume") ? message.optDouble("volume", 1) : 1,
                                    message.has("speed") ? message.optDouble("speed", 1) : 1);
                    if (!media.play(request)) {
                        throw new ProtocolException("Unsupported or missing media URL");
                    }
                    break;
                case OPCODE_PAUSE:
                    media.pause();
                    break;
                case OPCODE_RESUME:
                    media.resume();
                    break;
                case OPCODE_STOP:
                    media.stop();
                    break;
                case OPCODE_SEEK:
                    media.seek(message.optDouble("time", 0));
                    break;
                case OPCODE_SET_VOLUME:
                    media.setVolume(message.optDouble("volume", 1));
                    break;
                case OPCODE_SET_SPEED:
                    media.setSpeed(message.optDouble("speed", 1));
                    break;
                case OPCODE_PING:
                    send(OPCODE_PONG, null);
                    break;
                case OPCODE_VERSION:
                case OPCODE_INITIAL:
                    break;
                default:
                    Log.d(TAG, "Ignoring unsupported FCast opcode " + opcode);
            }
        }

        synchronized void send(int opcode, JSONObject body) throws IOException {
            byte[] payload = body == null
                    ? new byte[0]
                    : body.toString().getBytes(StandardCharsets.UTF_8);
            int size = payload.length + 1;
            if (size > MAX_PACKET_BYTES) {
                throw new IOException("FCast response exceeds packet limit");
            }
            output.write(ByteBuffer.allocate(4)
                    .order(ByteOrder.LITTLE_ENDIAN)
                    .putInt(size)
                    .array());
            output.write(opcode);
            output.write(payload);
            output.flush();
        }

        void close() {
            try {
                socket.close();
            } catch (IOException ignored) {
                // Client is already disconnected.
            }
        }

        private int readLittleEndianInt() throws IOException {
            return ByteBuffer.wrap(readExactly(4))
                    .order(ByteOrder.LITTLE_ENDIAN)
                    .getInt();
        }

        private byte[] readExactly(int count) throws IOException {
            byte[] data = new byte[count];
            int offset = 0;
            while (offset < count) {
                int read = input.read(data, offset, count - offset);
                if (read < 0) {
                    throw new EOFException();
                }
                offset += read;
            }
            return data;
        }
    }

    private static int playbackState(String state) {
        if ("playing".equals(state)) {
            return 1;
        }
        if ("paused".equals(state) || "buffering".equals(state)) {
            return 2;
        }
        return 0;
    }

    private static final class ProtocolException extends Exception {
        ProtocolException(String message) {
            super(message);
        }
    }
}
