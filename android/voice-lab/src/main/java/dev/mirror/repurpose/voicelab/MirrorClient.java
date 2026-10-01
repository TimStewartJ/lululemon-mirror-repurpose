package dev.mirror.repurpose.voicelab;

import java.io.IOException;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** Sends one request to Mirror Home on the same device, as a paired client would. */
final class MirrorClient {
    private static final String BASE = "http://127.0.0.1:8787";

    private final String token;

    MirrorClient(String token) {
        this.token = token;
    }

    /** The HTTP status Mirror Home answered with. */
    int send(Commands.Request request) throws IOException {
        HttpURLConnection connection =
                (HttpURLConnection) new URL(BASE + request.path).openConnection();
        try {
            connection.setConnectTimeout(3000);
            connection.setReadTimeout(5000);
            connection.setRequestMethod(request.method);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            if (!request.method.equals("GET")) {
                byte[] body = (request.body.isEmpty() ? "{}" : request.body)
                        .getBytes(StandardCharsets.UTF_8);
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setFixedLengthStreamingMode(body.length);
                try (OutputStream output = connection.getOutputStream()) {
                    output.write(body);
                }
            }
            return connection.getResponseCode();
        } finally {
            connection.disconnect();
        }
    }
}
