package dev.mirror.repurpose;

import java.io.IOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.Locale;

/**
 * Plays the mascots through a scene and writes every frame down as shapes,
 * for tools/mascots.py to make pictures of. It needs no Android: the tool
 * compiles it with the mascots and runs it on the computer.
 *
 * <p>Arguments: the file to write, frames a second, and the scene as steps
 * separated by commas. A step is {@code mood:listening} to put the mascot in
 * a mood, {@code speak:1.6:curious} to have it say something for 1.6
 * seconds and then be curious, {@code nod}, or {@code run:2} to let two
 * seconds pass.
 */
final class MascotFilm {
    private MascotFilm() {
    }

    public static void main(String[] arguments) throws IOException {
        int rate = Integer.parseInt(arguments[1]);
        String[] steps = arguments[2].split(",");
        try (Writer out = Files.newBufferedWriter(Paths.get(arguments[0]), StandardCharsets.UTF_8)) {
            out.write("{\"fps\":" + rate + ",\"mascots\":[");
            boolean first = true;
            for (Mascot mascot : Mascot.all()) {
                out.write((first ? "" : ",") + "{\"id\":\"" + mascot.id + "\",\"name\":\"" + mascot.name + "\",\"frames\":[");
                first = false;
                play(mascot, rate, steps, out);
                out.write("]}");
            }
            out.write("]}");
        }
    }

    private static void play(Mascot mascot, int rate, String[] steps, Writer out) throws IOException {
        MascotRig rig = new MascotRig();
        MascotRecorder recorder = new MascotRecorder();
        boolean first = true;
        for (String step : steps) {
            String[] parts = step.trim().split(":");
            switch (parts[0]) {
                case "mood":
                    rig.show(MascotRig.Mood.valueOf(parts[1].toUpperCase(Locale.ROOT)));
                    break;
                case "speak":
                    rig.speak(Float.parseFloat(parts[1]), MascotRig.Mood.valueOf(parts[2].toUpperCase(Locale.ROOT)));
                    break;
                case "nod":
                    rig.nod();
                    break;
                case "run":
                    int frames = Math.round(Float.parseFloat(parts[1]) * rate);
                    for (int frame = 0; frame < frames; frame++) {
                        rig.step(1f / rate);
                        recorder.clear();
                        mascot.render(recorder, rig.pose(), rig.clock());
                        out.write(first ? "[" : ",[");
                        first = false;
                        write(recorder, out);
                        out.write("]");
                    }
                    break;
                default:
                    throw new IllegalArgumentException("Not a step of a scene: " + step);
            }
        }
    }

    /** A frame: each shape as [rgb, alpha, width, closed, x0, y0, x1, y1, ...]. */
    private static void write(MascotRecorder recorder, Writer out) throws IOException {
        boolean first = true;
        for (MascotRecorder.Shape shape : recorder.shapes) {
            StringBuilder line = new StringBuilder(first ? "[" : ",[");
            first = false;
            line.append(shape.rgb).append(',').append(number(shape.alpha)).append(',').append(number(shape.width))
                    .append(',').append(shape.closed ? 1 : 0);
            for (float value : shape.points) {
                line.append(',').append(number(value));
            }
            out.write(line.append(']').toString());
        }
    }

    private static String number(float value) {
        return String.format(Locale.ROOT, "%.4f", value);
    }
}
