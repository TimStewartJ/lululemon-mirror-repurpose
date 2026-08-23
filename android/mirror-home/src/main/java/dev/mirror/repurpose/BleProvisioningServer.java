package dev.mirror.repurpose;

import android.Manifest;
import android.annotation.SuppressLint;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattDescriptor;
import android.bluetooth.BluetoothGattServer;
import android.bluetooth.BluetoothGattServerCallback;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.bluetooth.le.AdvertiseCallback;
import android.bluetooth.le.AdvertiseData;
import android.bluetooth.le.AdvertiseSettings;
import android.bluetooth.le.BluetoothLeAdvertiser;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.ParcelUuid;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.Iterator;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

@SuppressLint("MissingPermission")
public final class BleProvisioningServer {
    public static final UUID SERVICE_UUID =
            UUID.fromString("7d7a0001-6d69-7272-6f72-726570757270");
    public static final UUID REQUEST_UUID =
            UUID.fromString("7d7a0002-6d69-7272-6f72-726570757270");
    public static final UUID RESPONSE_UUID =
            UUID.fromString("7d7a0003-6d69-7272-6f72-726570757270");

    private static final UUID CLIENT_CONFIGURATION_UUID =
            UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    private static final String TAG = "BleProvisioning";
    private static final int MAX_REQUEST_BYTES = 2048;

    private static volatile String status = "stopped";

    private final Context context;
    private final PairingManager pairing;
    private final WifiProvisioner wifi;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final Map<String, ByteArrayOutputStream> requestBuffers =
            new ConcurrentHashMap<>();
    private final Map<String, Integer> mtuByDevice = new ConcurrentHashMap<>();
    private final Object notificationLock = new Object();
    private final ArrayDeque<PendingNotification> notifications = new ArrayDeque<>();
    private boolean notificationInFlight;

    private BluetoothAdapter adapter;
    private BluetoothGattServer gattServer;
    private BluetoothLeAdvertiser advertiser;
    private BluetoothGattCharacteristic responseCharacteristic;
    private AdvertiseCallback advertiseCallback;
    private boolean stopped;

    public BleProvisioningServer(
            Context context,
            PairingManager pairing,
            WifiProvisioner wifi) {
        this.context = context.getApplicationContext();
        this.pairing = pairing;
        this.wifi = wifi;
    }

    public void start() {
        stopped = false;
        attemptStart();
    }

    private void attemptStart() {
        if (stopped || gattServer != null) {
            return;
        }
        if (android.os.Build.VERSION.SDK_INT >= 31
                && (context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)
                        != PackageManager.PERMISSION_GRANTED
                || context.checkSelfPermission(Manifest.permission.BLUETOOTH_ADVERTISE)
                        != PackageManager.PERMISSION_GRANTED)) {
            status = "permission-required";
            return;
        }
        BluetoothManager manager =
                (BluetoothManager) context.getSystemService(Context.BLUETOOTH_SERVICE);
        adapter = manager == null ? null : manager.getAdapter();
        if (adapter == null) {
            status = "unsupported";
            Log.w(TAG, "BLE peripheral advertising is unavailable");
            return;
        }
        if (!adapter.isEnabled() || !adapter.isMultipleAdvertisementSupported()) {
            status = "waiting-for-bluetooth";
            mainHandler.postDelayed(this::attemptStart, 5000);
            return;
        }

        gattServer = manager.openGattServer(context, callback);
        if (gattServer == null) {
            status = "waiting-for-bluetooth";
            mainHandler.postDelayed(this::attemptStart, 5000);
            return;
        }

        BluetoothGattService service =
                new BluetoothGattService(SERVICE_UUID, BluetoothGattService.SERVICE_TYPE_PRIMARY);
        BluetoothGattCharacteristic request = new BluetoothGattCharacteristic(
                REQUEST_UUID,
                BluetoothGattCharacteristic.PROPERTY_WRITE
                        | BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE,
                BluetoothGattCharacteristic.PERMISSION_WRITE_ENCRYPTED);
        responseCharacteristic = new BluetoothGattCharacteristic(
                RESPONSE_UUID,
                BluetoothGattCharacteristic.PROPERTY_READ
                        | BluetoothGattCharacteristic.PROPERTY_NOTIFY,
                BluetoothGattCharacteristic.PERMISSION_READ_ENCRYPTED);
        responseCharacteristic.addDescriptor(
                new BluetoothGattDescriptor(
                        CLIENT_CONFIGURATION_UUID,
                        BluetoothGattDescriptor.PERMISSION_READ_ENCRYPTED
                                | BluetoothGattDescriptor.PERMISSION_WRITE_ENCRYPTED));
        service.addCharacteristic(request);
        service.addCharacteristic(responseCharacteristic);
        status = "starting";
        gattServer.addService(service);
    }

    public void stop() {
        stopped = true;
        mainHandler.removeCallbacksAndMessages(null);
        status = "stopped";
        if (advertiser != null && advertiseCallback != null) {
            advertiser.stopAdvertising(advertiseCallback);
        }
        advertiseCallback = null;
        advertiser = null;
        if (gattServer != null) {
            gattServer.close();
            gattServer = null;
        }
        requestBuffers.clear();
        mtuByDevice.clear();
        synchronized (notificationLock) {
            notifications.clear();
            notificationInFlight = false;
        }
        worker.shutdownNow();
    }

    public static String lastKnownStatus() {
        return status;
    }

    private void advertise() {
        advertiser = adapter.getBluetoothLeAdvertiser();
        if (advertiser == null) {
            status = "unsupported";
            return;
        }
        AdvertiseSettings settings = new AdvertiseSettings.Builder()
                .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY)
                .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_HIGH)
                .setConnectable(true)
                .setTimeout(0)
                .build();
        AdvertiseData data = new AdvertiseData.Builder()
                .addServiceUuid(new ParcelUuid(SERVICE_UUID))
                .build();
        AdvertiseData scanResponse = new AdvertiseData.Builder()
                .setIncludeDeviceName(true)
                .build();
        advertiseCallback = new AdvertiseCallback() {
            @Override
            public void onStartSuccess(AdvertiseSettings settingsInEffect) {
                status = "advertising";
                Log.i(TAG, "BLE provisioning service is advertising");
            }

            @Override
            public void onStartFailure(int errorCode) {
                status = "error:" + errorCode;
                Log.w(TAG, "BLE advertising failed: " + errorCode);
            }
        };
        advertiser.startAdvertising(settings, data, scanResponse, advertiseCallback);
    }

    private final BluetoothGattServerCallback callback = new BluetoothGattServerCallback() {
        @Override
        public void onServiceAdded(int statusCode, BluetoothGattService service) {
            if (statusCode == BluetoothGatt.GATT_SUCCESS) {
                advertise();
            } else {
                status = "service-error:" + statusCode;
            }
        }

        @Override
        public void onConnectionStateChange(
                BluetoothDevice device,
                int statusCode,
                int newState) {
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                requestBuffers.put(device.getAddress(), new ByteArrayOutputStream());
                mtuByDevice.put(device.getAddress(), 23);
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                requestBuffers.remove(device.getAddress());
                mtuByDevice.remove(device.getAddress());
                removePendingNotifications(device);
            }
        }

        @Override
        public void onMtuChanged(BluetoothDevice device, int mtu) {
            mtuByDevice.put(device.getAddress(), Math.max(23, mtu));
        }

        @Override
        public void onCharacteristicWriteRequest(
                BluetoothDevice device,
                int requestId,
                BluetoothGattCharacteristic characteristic,
                boolean preparedWrite,
                boolean responseNeeded,
                int offset,
                byte[] value) {
            if (!REQUEST_UUID.equals(characteristic.getUuid())
                    || preparedWrite
                    || offset != 0
                    || value == null) {
                respondToWrite(device, requestId, responseNeeded, BluetoothGatt.GATT_FAILURE);
                return;
            }

            ByteArrayOutputStream buffer;
            synchronized (requestBuffers) {
                buffer = requestBuffers.get(device.getAddress());
                if (buffer == null) {
                    buffer = new ByteArrayOutputStream();
                    requestBuffers.put(device.getAddress(), buffer);
                }
            }
            if (buffer.size() + value.length > MAX_REQUEST_BYTES) {
                buffer.reset();
                respondToWrite(device, requestId, responseNeeded, BluetoothGatt.GATT_FAILURE);
                return;
            }
            synchronized (buffer) {
                buffer.write(value, 0, value.length);
                respondToWrite(device, requestId, responseNeeded, BluetoothGatt.GATT_SUCCESS);

                byte[] accumulated = buffer.toByteArray();
                int newline;
                int consumed = 0;
                while ((newline = indexOf(accumulated, consumed, (byte) '\n')) >= 0) {
                    byte[] request = Arrays.copyOfRange(accumulated, consumed, newline);
                    consumed = newline + 1;
                    worker.execute(new Runnable() {
                        @Override
                        public void run() {
                            handleRequest(device, request);
                        }
                    });
                }
                if (consumed > 0) {
                    buffer.reset();
                    buffer.write(accumulated, consumed, accumulated.length - consumed);
                }
            }
        }

        @Override
        public void onCharacteristicReadRequest(
                BluetoothDevice device,
                int requestId,
                int offset,
                BluetoothGattCharacteristic characteristic) {
            BluetoothGattServer currentServer = gattServer;
            if (currentServer == null) {
                return;
            }
            if (!RESPONSE_UUID.equals(characteristic.getUuid())) {
                currentServer.sendResponse(
                        device,
                        requestId,
                        BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED,
                        0,
                        null);
                return;
            }
            byte[] value = statusJson().getBytes(StandardCharsets.UTF_8);
            if (offset > value.length) {
                currentServer.sendResponse(
                        device,
                        requestId,
                        BluetoothGatt.GATT_INVALID_OFFSET,
                        offset,
                        null);
                return;
            }
            currentServer.sendResponse(
                    device,
                    requestId,
                    BluetoothGatt.GATT_SUCCESS,
                    offset,
                    Arrays.copyOfRange(value, offset, value.length));
        }

        @Override
        public void onDescriptorWriteRequest(
                BluetoothDevice device,
                int requestId,
                BluetoothGattDescriptor descriptor,
                boolean preparedWrite,
                boolean responseNeeded,
                int offset,
                byte[] value) {
            if (CLIENT_CONFIGURATION_UUID.equals(descriptor.getUuid()) && value != null) {
                descriptor.setValue(value);
                respondToWrite(device, requestId, responseNeeded, BluetoothGatt.GATT_SUCCESS);
            } else {
                respondToWrite(device, requestId, responseNeeded, BluetoothGatt.GATT_FAILURE);
            }
        }

        @Override
        public void onNotificationSent(BluetoothDevice device, int statusCode) {
            synchronized (notificationLock) {
                PendingNotification current = notifications.peekFirst();
                if (current != null && current.address.equals(device.getAddress())) {
                    notifications.removeFirst();
                }
                notificationInFlight = false;
            }
            sendNextNotification();
        }
    };

    private void handleRequest(BluetoothDevice device, byte[] requestBytes) {
        JSONObject response = new JSONObject();
        try {
            JSONObject request =
                    new JSONObject(new String(requestBytes, StandardCharsets.UTF_8));
            if (!"provision".equals(request.optString("type"))) {
                throw new IllegalArgumentException("Unsupported request type");
            }
            String ssid = request.optString("ssid", null);
            String passphrase = request.optString("passphrase", null);
            if (!InputValidator.validSsid(ssid)
                    || !InputValidator.validWpaPassphrase(passphrase)) {
                throw new IllegalArgumentException("Invalid Wi-Fi credentials");
            }
            String token = pairing.pair(request.optString("code", null));
            if (token == null) {
                throw new SecurityException("Invalid or expired pairing code");
            }

            WifiProvisioner.Result wifiResult = wifi.configure(
                    ssid,
                    passphrase,
                    request.optBoolean("hidden", false));
            String ipAddress = wifiResult.success ? waitForIpAddress() : "";
            boolean connected = wifiResult.success && !ipAddress.isEmpty();
            response.put("ok", connected);
            response.put("message", connected ? wifiResult.message : "Wi-Fi did not acquire an address");
            if (connected) {
                response.put("token", token);
                response.put(
                        "ipAddress",
                        ipAddress);
                response.put("apiPort", ControlServerService.PORT);
                Intent changed =
                        new Intent(ControlServerService.ACTION_CONFIGURATION_CHANGED);
                changed.setPackage(context.getPackageName());
                context.sendBroadcast(changed);
            } else {
                pairing.revoke();
                Intent changed =
                        new Intent(ControlServerService.ACTION_CONFIGURATION_CHANGED);
                changed.setPackage(context.getPackageName());
                context.sendBroadcast(changed);
            }
        } catch (Exception error) {
            try {
                response.put("ok", false);
                response.put("error", error.getMessage());
            } catch (JSONException ignored) {
                // Keys and values are valid JSON values.
            }
        }
        notifyJson(device, response);
    }

    private String waitForIpAddress() {
        WifiManager manager =
                (WifiManager) context.getSystemService(Context.WIFI_SERVICE);
        for (int attempt = 0; attempt < 120; attempt++) {
            WifiInfo info = manager == null ? null : manager.getConnectionInfo();
            if (info != null && info.getNetworkId() >= 0 && info.getIpAddress() != 0) {
                return WifiProvisioner.ipAddress(info.getIpAddress());
            }
            try {
                Thread.sleep(500);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                return "";
            }
        }
        return "";
    }

    private void notifyJson(BluetoothDevice device, JSONObject response) {
        if (gattServer == null || responseCharacteristic == null) {
            return;
        }
        byte[] bytes = (response.toString() + "\n").getBytes(StandardCharsets.UTF_8);
        Integer negotiatedMtu = mtuByDevice.get(device.getAddress());
        int mtu = negotiatedMtu == null ? 23 : negotiatedMtu;
        int chunkSize = Math.max(1, mtu - 3);
        synchronized (notificationLock) {
            for (int offset = 0; offset < bytes.length; offset += chunkSize) {
                int end = Math.min(bytes.length, offset + chunkSize);
                notifications.addLast(
                        new PendingNotification(
                                device,
                                Arrays.copyOfRange(bytes, offset, end)));
            }
        }
        sendNextNotification();
    }

    private void respondToWrite(
            BluetoothDevice device,
            int requestId,
            boolean responseNeeded,
            int statusCode) {
        if (responseNeeded && gattServer != null) {
            gattServer.sendResponse(device, requestId, statusCode, 0, null);
        }
    }

    private String statusJson() {
        JSONObject response = new JSONObject();
        try {
            response.put("service", "mirror-repurpose");
            response.put("paired", pairing.isPaired());
            response.put("status", status);
        } catch (JSONException ignored) {
            // Keys and values are valid JSON values.
        }
        return response.toString();
    }

    private void sendNextNotification() {
        synchronized (notificationLock) {
            if (notificationInFlight || gattServer == null || responseCharacteristic == null) {
                return;
            }
            while (!notifications.isEmpty()) {
                PendingNotification next = notifications.peekFirst();
                responseCharacteristic.setValue(next.value);
                if (gattServer.notifyCharacteristicChanged(
                        next.device,
                        responseCharacteristic,
                        false)) {
                    notificationInFlight = true;
                    return;
                }
                notifications.removeFirst();
            }
        }
    }

    private void removePendingNotifications(BluetoothDevice device) {
        synchronized (notificationLock) {
            PendingNotification current = notifications.peekFirst();
            if (current != null && current.address.equals(device.getAddress())) {
                notificationInFlight = false;
            }
            Iterator<PendingNotification> iterator = notifications.iterator();
            while (iterator.hasNext()) {
                if (iterator.next().address.equals(device.getAddress())) {
                    iterator.remove();
                }
            }
        }
        sendNextNotification();
    }

    private static int indexOf(byte[] data, int start, byte target) {
        for (int index = start; index < data.length; index++) {
            if (data[index] == target) {
                return index;
            }
        }
        return -1;
    }

    private static final class PendingNotification {
        final String address;
        final BluetoothDevice device;
        final byte[] value;

        PendingNotification(BluetoothDevice device, byte[] value) {
            this.address = device.getAddress();
            this.device = device;
            this.value = value;
        }
    }
}
