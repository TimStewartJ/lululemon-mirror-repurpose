package dev.mirror.repurpose;

import android.content.Context;
import android.content.Intent;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.SecureRandom;
import java.security.cert.Certificate;
import java.security.cert.CertificateFactory;
import java.util.Locale;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509TrustManager;

public final class WeatherProvider {
    public static final String ACTION_WEATHER_CHANGED =
            "dev.mirror.repurpose.WEATHER_CHANGED";

    private static final String TAG = "MirrorWeather";
    private static final int MAX_RESPONSE_BYTES = 512 * 1024;
    private static final long REFRESH_INTERVAL_MS = 30 * 60 * 1000L;
    private static final long RETRY_INTERVAL_MS = 5 * 60 * 1000L;
    private static final long STALE_AFTER_MS = 90 * 60 * 1000L;
    private static final long INITIAL_DELAY_MS = 8_000L;
    private static volatile WeatherProvider instance;

    private final Context context;
    private final ConfigStore configStore;
    private final File cacheFile;
    private final File cacheBackupFile;
    private final SSLSocketFactory sslSocketFactory;
    private final ScheduledExecutorService executor =
            Executors.newSingleThreadScheduledExecutor();
    private ScheduledFuture<?> scheduledRefresh;
    private WeatherData data;
    private String dataCacheKey = "";
    private String error = "";
    private boolean refreshing;
    private boolean refreshRequested;
    private long nextRefreshAt;

    private WeatherProvider(Context context) {
        this.context = context.getApplicationContext();
        configStore = new ConfigStore(this.context);
        cacheFile = new File(this.context.getFilesDir(), "weather-cache.json");
        cacheBackupFile = new File(this.context.getFilesDir(), "weather-cache.json.bak");
        sslSocketFactory = createSocketFactory(this.context);
        loadCache();
        schedule(INITIAL_DELAY_MS);
    }

    public static WeatherProvider getInstance(Context context) {
        if (instance == null) {
            synchronized (WeatherProvider.class) {
                if (instance == null) {
                    instance = new WeatherProvider(context);
                }
            }
        }
        return instance;
    }

    public synchronized JSONObject snapshot(boolean includeCoordinates) throws JSONException {
        WeatherConfig config = configStore.getWeatherConfig();
        long now = System.currentTimeMillis();
        boolean compatibleData =
                config.enabled && data != null && config.cacheKey().equals(dataCacheKey);
        boolean stale = compatibleData && now - data.fetchedAt() > STALE_AFTER_MS;
        String state;
        if (!config.enabled) {
            state = "unconfigured";
        } else if (refreshing) {
            state = compatibleData ? (stale ? "stale" : "ready") : "refreshing";
        } else if (compatibleData) {
            state = stale ? "stale" : "ready";
        } else {
            state = error.isEmpty() ? "waiting" : "error";
        }
        return new JSONObject()
                .put("config", config.toJson(includeCoordinates))
                .put("state", state)
                .put("refreshing", refreshing)
                .put("stale", stale)
                .put("updatedAt", compatibleData ? data.fetchedAt() : JSONObject.NULL)
                .put("nextRefreshAt", nextRefreshAt > 0L ? nextRefreshAt : JSONObject.NULL)
                .put("error", error.isEmpty() ? JSONObject.NULL : error)
                .put("data", compatibleData ? data.toJson() : JSONObject.NULL);
    }

    public synchronized void update(JSONObject value) throws JSONException {
        WeatherConfig config = WeatherConfig.parse(value);
        configStore.setWeatherConfig(config);
        if (!config.enabled) {
            error = "";
            refreshRequested = false;
        } else if (data == null || !config.cacheKey().equals(dataCacheKey)) {
            error = "";
        }
        if (config.enabled) {
            if (refreshing) {
                refreshRequested = true;
            } else {
                schedule(0L);
            }
        } else {
            schedule(REFRESH_INTERVAL_MS);
        }
        broadcast();
    }

    public synchronized void refreshNow() {
        WeatherConfig config = configStore.getWeatherConfig();
        if (!config.enabled) {
            return;
        }
        if (refreshing) {
            refreshRequested = true;
            return;
        }
        schedule(0L);
    }

    public synchronized void refreshIfDue() {
        WeatherConfig config = configStore.getWeatherConfig();
        if (!config.enabled) {
            return;
        }
        if (refreshing) {
            refreshRequested = true;
            return;
        }
        if (data == null
                || !config.cacheKey().equals(dataCacheKey)
                || System.currentTimeMillis() - data.fetchedAt() >= REFRESH_INTERVAL_MS) {
            schedule(0L);
        }
    }

    public JSONObject searchLocations(String query) throws IOException, JSONException {
        String normalized = query == null ? "" : query.trim();
        if (normalized.length() < 2 || normalized.length() > 80) {
            throw new JSONException("Location search must contain 2-80 characters");
        }
        for (int index = 0; index < normalized.length(); index++) {
            if (Character.isISOControl(normalized.charAt(index))) {
                throw new JSONException("Location search is invalid");
            }
        }
        // A town with its state or country is no town's name to the service: see WeatherPlaces.
        JSONArray elsewhere = null;
        for (WeatherPlaces.Reading reading : WeatherPlaces.readings(normalized)) {
            URL url = new URL(
                    "https://geocoding-api.open-meteo.com/v1/search?language=en&format=json&count="
                            + reading.count() + "&name=" + URLEncoder.encode(reading.name, "UTF-8"));
            JSONObject source;
            try {
                source = fetchJson(url);
            } catch (IOException errorValue) {
                Log.e(TAG, "Unable to search weather locations", errorValue);
                throw errorValue;
            }
            JSONArray found = source.optJSONArray("results");
            JSONArray results = WeatherPlaces.pick(found, reading.region);
            if (results.length() > 0) {
                return new JSONObject().put("results", results);
            }
            if (elsewhere == null && !reading.region.isEmpty()) {
                JSONArray others = WeatherPlaces.pick(found, "");
                elsewhere = others.length() > 0 ? others : null;
            }
        }
        JSONObject nothing = new JSONObject().put("results", new JSONArray());
        // Towns of that name that lie elsewhere, so that whoever asked can see what there is.
        return elsewhere == null ? nothing : nothing.put("elsewhere", elsewhere);
    }

    private synchronized void schedule(long delayMs) {
        if (scheduledRefresh != null) {
            scheduledRefresh.cancel(false);
        }
        WeatherConfig config = configStore.getWeatherConfig();
        if (!config.enabled) {
            nextRefreshAt = 0L;
            return;
        }
        nextRefreshAt = System.currentTimeMillis() + delayMs;
        scheduledRefresh = executor.schedule(new Runnable() {
            @Override
            public void run() {
                fetch();
            }
        }, Math.max(0L, delayMs), TimeUnit.MILLISECONDS);
    }

    private void fetch() {
        WeatherConfig config;
        synchronized (this) {
            config = configStore.getWeatherConfig();
            if (!config.enabled || refreshing) {
                return;
            }
            refreshing = true;
            nextRefreshAt = 0L;
        }
        broadcast();

        long nextDelay = REFRESH_INTERVAL_MS;
        try {
            JSONObject response = fetchJson(requestUrl(config));
            WeatherData nextData =
                    WeatherData.parse(response, config, System.currentTimeMillis());
            synchronized (this) {
                WeatherConfig active = configStore.getWeatherConfig();
                if (active.enabled && active.cacheKey().equals(config.cacheKey())) {
                    writeCache(config.cacheKey(), nextData);
                    data = nextData;
                    dataCacheKey = config.cacheKey();
                    error = "";
                } else {
                    refreshRequested = active.enabled;
                }
            }
        } catch (IOException | JSONException errorValue) {
            Log.e(TAG, "Unable to refresh weather", errorValue);
            synchronized (this) {
                WeatherConfig active = configStore.getWeatherConfig();
                if (active.enabled && active.cacheKey().equals(config.cacheKey())) {
                    error = conciseError(errorValue);
                } else {
                    refreshRequested = active.enabled;
                }
            }
            nextDelay = RETRY_INTERVAL_MS;
        } finally {
            synchronized (this) {
                refreshing = false;
                WeatherConfig active = configStore.getWeatherConfig();
                if (active.enabled) {
                    boolean immediate = refreshRequested
                            || !active.cacheKey().equals(config.cacheKey());
                    refreshRequested = false;
                    schedule(immediate ? 0L : nextDelay);
                }
            }
            broadcast();
        }
    }

    private static URL requestUrl(WeatherConfig config) throws IOException {
        StringBuilder value = new StringBuilder("https://api.open-meteo.com/v1/forecast");
        value.append("?latitude=").append(String.format(Locale.US, "%.5f", config.latitude));
        value.append("&longitude=").append(String.format(Locale.US, "%.5f", config.longitude));
        value.append("&current=temperature_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m");
        value.append("&hourly=temperature_2m,precipitation_probability,weather_code");
        value.append("&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_probability_max");
        value.append("&forecast_days=3&timeformat=unixtime&timezone=auto");
        if (WeatherConfig.UNITS_US.equals(config.units)) {
            value.append("&temperature_unit=fahrenheit");
            value.append("&wind_speed_unit=mph");
            value.append("&precipitation_unit=inch");
        }
        return new URL(value.toString());
    }

    private JSONObject fetchJson(URL url) throws IOException, JSONException {
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        try {
            if (connection instanceof HttpsURLConnection) {
                ((HttpsURLConnection) connection).setSSLSocketFactory(sslSocketFactory);
            }
            connection.setConnectTimeout(10_000);
            connection.setReadTimeout(15_000);
            connection.setUseCaches(false);
            connection.setRequestProperty(
                    "User-Agent",
                    "Mirror-Home/" + BuildConfig.VERSION_NAME);
            int status = connection.getResponseCode();
            if (status != HttpURLConnection.HTTP_OK) {
                throw new IOException("Weather service returned HTTP " + status);
            }
            int contentLength = connection.getContentLength();
            if (contentLength > MAX_RESPONSE_BYTES) {
                throw new IOException("Weather response exceeds size limit");
            }
            try (InputStream input = connection.getInputStream();
                    ByteArrayOutputStream output = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[8192];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    output.write(buffer, 0, count);
                    if (output.size() > MAX_RESPONSE_BYTES) {
                        throw new IOException("Weather response exceeds size limit");
                    }
                }
                return new JSONObject(
                        new String(output.toByteArray(), StandardCharsets.UTF_8));
            }
        } finally {
            connection.disconnect();
        }
    }

    private synchronized void loadCache() {
        if (!loadCacheFile(cacheFile)) {
            loadCacheFile(cacheBackupFile);
        }
    }

    private boolean loadCacheFile(File source) {
        if (!source.isFile() || source.length() > MAX_RESPONSE_BYTES) {
            return false;
        }
        try (FileInputStream input = new FileInputStream(source);
                ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) {
                output.write(buffer, 0, count);
                if (output.size() > MAX_RESPONSE_BYTES) {
                    throw new IOException("Cached weather exceeds size limit");
                }
            }
            JSONObject cache = new JSONObject(
                    new String(output.toByteArray(), StandardCharsets.UTF_8));
            if (cache.optInt("version", 0) != 1) {
                return false;
            }
            dataCacheKey = cache.getString("cacheKey");
            data = WeatherData.fromCache(cache.getJSONObject("data"));
            return true;
        } catch (IOException | JSONException cacheError) {
            Log.w(TAG, "Ignoring invalid weather cache", cacheError);
            return false;
        }
    }

    private void writeCache(String cacheKey, WeatherData nextData)
            throws IOException, JSONException {
        File temporary = new File(cacheFile.getParentFile(), cacheFile.getName() + ".tmp");
        byte[] bytes = new JSONObject()
                .put("version", 1)
                .put("cacheKey", cacheKey)
                .put("data", nextData.toJson())
                .toString()
                .getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_RESPONSE_BYTES) {
            throw new IOException("Normalized weather cache exceeds size limit");
        }
        try (FileOutputStream output = new FileOutputStream(temporary)) {
            output.write(bytes);
            output.getFD().sync();
        }
        if (cacheBackupFile.exists() && !cacheBackupFile.delete()) {
            throw new IOException("Unable to replace weather cache backup");
        }
        if (cacheFile.exists() && !cacheFile.renameTo(cacheBackupFile)) {
            throw new IOException("Unable to preserve previous weather cache");
        }
        if (!temporary.renameTo(cacheFile)) {
            if (!cacheFile.exists() && cacheBackupFile.exists()) {
                cacheBackupFile.renameTo(cacheFile);
            }
            throw new IOException("Unable to finalize weather cache");
        }
        if (cacheBackupFile.exists() && !cacheBackupFile.delete()) {
            Log.w(TAG, "Unable to delete previous weather cache backup");
        }
    }

    private void broadcast() {
        Intent intent = new Intent(ACTION_WEATHER_CHANGED);
        intent.setPackage(context.getPackageName());
        context.sendBroadcast(intent);
    }

    private static String conciseError(Exception error) {
        String message = error.getMessage();
        if (message == null || message.isEmpty()) {
            return "Weather refresh failed";
        }
        return message.length() > 160 ? message.substring(0, 160) : message;
    }

    private static SSLSocketFactory createSocketFactory(Context context) {
        try {
            TrustManagerFactory systemFactory = TrustManagerFactory.getInstance(
                    TrustManagerFactory.getDefaultAlgorithm());
            systemFactory.init((KeyStore) null);
            X509TrustManager system = findTrustManager(systemFactory.getTrustManagers());

            CertificateFactory certificateFactory =
                    CertificateFactory.getInstance("X.509");
            Certificate root;
            try (InputStream input =
                    context.getResources().openRawResource(R.raw.isrg_root_x1)) {
                root = certificateFactory.generateCertificate(input);
            }
            KeyStore keyStore = KeyStore.getInstance(KeyStore.getDefaultType());
            keyStore.load(null, null);
            keyStore.setCertificateEntry("isrg-root-x1", root);
            TrustManagerFactory bundledFactory = TrustManagerFactory.getInstance(
                    TrustManagerFactory.getDefaultAlgorithm());
            bundledFactory.init(keyStore);
            X509TrustManager bundled = findTrustManager(bundledFactory.getTrustManagers());

            SSLContext sslContext = SSLContext.getInstance("TLSv1.2");
            sslContext.init(
                    null,
                    new TrustManager[]{new CombinedTrustManager(system, bundled)},
                    new SecureRandom());
            return sslContext.getSocketFactory();
        } catch (Exception error) {
            throw new IllegalStateException("Unable to initialize weather HTTPS trust", error);
        }
    }

    private static X509TrustManager findTrustManager(TrustManager[] managers) {
        for (TrustManager manager : managers) {
            if (manager instanceof X509TrustManager) {
                return (X509TrustManager) manager;
            }
        }
        throw new IllegalStateException("No X.509 trust manager is available");
    }

    private static final class CombinedTrustManager implements X509TrustManager {
        private final X509TrustManager system;
        private final X509TrustManager bundled;

        CombinedTrustManager(X509TrustManager system, X509TrustManager bundled) {
            this.system = system;
            this.bundled = bundled;
        }

        @Override
        public void checkClientTrusted(
                java.security.cert.X509Certificate[] chain,
                String authType) throws java.security.cert.CertificateException {
            system.checkClientTrusted(chain, authType);
        }

        @Override
        public void checkServerTrusted(
                java.security.cert.X509Certificate[] chain,
                String authType) throws java.security.cert.CertificateException {
            try {
                system.checkServerTrusted(chain, authType);
            } catch (java.security.cert.CertificateException systemError) {
                bundled.checkServerTrusted(chain, authType);
            }
        }

        @Override
        public java.security.cert.X509Certificate[] getAcceptedIssuers() {
            java.security.cert.X509Certificate[] systemIssuers =
                    system.getAcceptedIssuers();
            java.security.cert.X509Certificate[] bundledIssuers =
                    bundled.getAcceptedIssuers();
            java.security.cert.X509Certificate[] combined =
                    new java.security.cert.X509Certificate[
                            systemIssuers.length + bundledIssuers.length];
            System.arraycopy(systemIssuers, 0, combined, 0, systemIssuers.length);
            System.arraycopy(
                    bundledIssuers,
                    0,
                    combined,
                    systemIssuers.length,
                    bundledIssuers.length);
            return combined;
        }
    }
}
