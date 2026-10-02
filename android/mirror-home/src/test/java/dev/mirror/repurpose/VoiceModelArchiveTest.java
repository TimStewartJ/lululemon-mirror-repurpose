package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

public final class VoiceModelArchiveTest {
    @Rule
    public final TemporaryFolder folder = new TemporaryFolder();

    private static Map<String, Integer> model(String prefix) {
        Map<String, Integer> files = new LinkedHashMap<>();
        files.put(prefix + "README", 20);
        files.put(prefix + "am/final.mdl", 3000);
        files.put(prefix + "conf/mfcc.conf", 40);
        files.put(prefix + "conf/model.conf", 60);
        files.put(prefix + "graph/HCLr.fst", 2000);
        files.put(prefix + "graph/Gr.fst", 2000);
        files.put(prefix + "graph/phones/word_boundary.int", 30);
        files.put(prefix + "ivector/final.ie", 900);
        return files;
    }

    private File zip(String name, Map<String, Integer> files) throws IOException {
        File archive = folder.newFile(name);
        try (ZipOutputStream output = new ZipOutputStream(new FileOutputStream(archive))) {
            for (Map.Entry<String, Integer> file : files.entrySet()) {
                output.putNextEntry(new ZipEntry(file.getKey()));
                if (!file.getKey().endsWith("/")) {
                    byte[] content = new byte[file.getValue()];
                    Arrays.fill(content, (byte) 7);
                    output.write(content);
                }
                output.closeEntry();
            }
        }
        return archive;
    }

    private String refusal(File archive, VoiceModelArchive.Limits limits) {
        File target = new File(folder.getRoot(), "out-" + System.nanoTime());
        IOException error = assertThrows(
                IOException.class, () -> VoiceModelArchive.unpack(archive, target, limits));
        return error.getMessage();
    }

    private String refusal(File archive) {
        return refusal(archive, new VoiceModelArchive.Limits(1 << 20, 64));
    }

    @Test
    public void aModelInItsOwnFolderIsUnpackedWithoutTheFolder() throws Exception {
        Map<String, Integer> files = model("vosk-model-small-en-us-0.15/");
        files.put("vosk-model-small-en-us-0.15/ivector/", 0);
        File target = new File(folder.getRoot(), "model");
        VoiceModelArchive.Contents contents = VoiceModelArchive.unpack(zip("model.zip", files), target);

        assertEquals("vosk-model-small-en-us-0.15", contents.name);
        assertEquals(8, contents.files);
        assertEquals(20 + 3000 + 40 + 60 + 2000 + 2000 + 30 + 900, contents.bytes);
        assertEquals(
                Arrays.asList("README", "am/final.mdl", "conf/mfcc.conf", "conf/model.conf",
                        "graph/Gr.fst", "graph/HCLr.fst", "graph/phones/word_boundary.int", "ivector/final.ie"),
                VoiceModelArchive.list(target));
        assertEquals(3000, new File(target, "am/final.mdl").length());
    }

    @Test
    public void aModelWithoutAFolderOfItsOwnIsUnpackedAsItIs() throws Exception {
        File target = new File(folder.getRoot(), "model");
        VoiceModelArchive.Contents contents = VoiceModelArchive.unpack(zip("flat.zip", model("")), target);
        assertEquals("model", contents.name);
        assertTrue(new File(target, "graph/Gr.fst").isFile());
    }

    @Test
    public void aFileNamedOutsideTheArchiveIsRefusedAndNothingIsWrittenThere() throws Exception {
        for (String name : new String[]{"../evil", "am/../../evil", "/etc/evil", "am//evil", "C:evil", "am\\evil", "./evil"}) {
            Map<String, Integer> files = model("");
            files.put(name, 10);
            assertEquals(name, "The archive names a file outside itself", refusal(zip("bad" + name.hashCode() + ".zip", files)));
        }
        assertFalse(new File(folder.getRoot(), "evil").exists());
        assertFalse(new File(folder.getRoot().getParentFile(), "evil").exists());
    }

    @Test
    public void whatIsNotASpeechModelIsRefusedWithTheReason() throws Exception {
        Map<String, Integer> files = model("");
        files.remove("am/final.mdl");
        assertEquals("This is not a speech model: am/final.mdl is missing", refusal(zip("a.zip", files)));

        files = model("");
        files.remove("graph/Gr.fst");
        assertEquals("This is not a speech model: its graph is missing", refusal(zip("b.zip", files)));

        files = model("");
        files.put("conf/model.conf", 0);
        assertEquals("This is not a speech model: conf/model.conf is missing", refusal(zip("c.zip", files)));

        File text = folder.newFile("notes.zip");
        Files.write(text.toPath(), "not a zip".getBytes());
        assertEquals("The upload is not a zip archive", refusal(text));

        assertEquals("The archive is empty", refusal(zip("empty.zip", new LinkedHashMap<>())));
    }

    @Test
    public void aLargeModelThatCannotTakeACommandListIsRefused() throws Exception {
        Map<String, Integer> files = model("");
        files.remove("graph/HCLr.fst");
        files.remove("graph/Gr.fst");
        files.put("graph/HCLG.fst", 5000);
        assertEquals(
                "This speech model cannot take a command list; use a small model",
                refusal(zip("big.zip", files)));
    }

    @Test
    public void anArchiveThatUnpacksTooLargeOrHoldsTooManyFilesIsRefused() throws Exception {
        assertEquals(
                "The model unpacks to more than 0 MB",
                refusal(zip("large.zip", model("")), new VoiceModelArchive.Limits(5000, 64)));
        assertEquals(
                "The archive holds more than 5 files",
                refusal(zip("many.zip", model("")), new VoiceModelArchive.Limits(1 << 20, 5)));
    }

    @Test
    public void aModelIsNeverUnpackedOverAnother() throws Exception {
        File target = folder.newFolder("taken");
        assertThrows(IOException.class, () -> VoiceModelArchive.unpack(zip("model.zip", model("")), target));
    }

    @Test
    public void theStoreKeepsOneModelAndSaysWhatItIs() throws Exception {
        VoiceModelStore store = new VoiceModelStore(new File(folder.getRoot(), "voice"));
        assertFalse(store.installed());
        assertNull(store.describe());

        JSONObject installed = store.install(zip("one.zip", model("first/")), "aa11", 1000L);
        assertTrue(store.installed());
        assertEquals("first", installed.getString("name"));
        assertEquals("aa11", store.describe().getString("sha256"));
        assertEquals(1000L, store.describe().getLong("installedAt"));
        assertEquals(8, store.describe().getInt("files"));

        store.install(zip("two.zip", model("second/")), "bb22", 2000L);
        assertEquals("second", store.describe().getString("name"));
        assertEquals(Arrays.asList("model", "model.json"), Arrays.asList(sorted(new File(folder.getRoot(), "voice"))));

        assertTrue(store.remove());
        assertFalse(store.installed());
        assertNull(store.describe());
        assertFalse(store.remove());
    }

    @Test
    public void aBadUploadLeavesTheModelThatWasThere() throws Exception {
        VoiceModelStore store = new VoiceModelStore(new File(folder.getRoot(), "voice"));
        store.install(zip("good.zip", model("good/")), "aa11", 1000L);
        Map<String, Integer> broken = model("");
        broken.remove("am/final.mdl");
        assertThrows(IOException.class, () -> store.install(zip("broken.zip", broken), "cc33", 3000L));

        assertTrue(store.installed());
        assertEquals("good", store.describe().getString("name"));
        assertEquals(Arrays.asList("model", "model.json"), Arrays.asList(sorted(new File(folder.getRoot(), "voice"))));
    }

    @Test
    public void anInstallationCutShortIsPutRightAtTheNextStart() throws Exception {
        File root = new File(folder.getRoot(), "voice");
        VoiceModelStore store = new VoiceModelStore(root);
        store.install(zip("good.zip", model("good/")), "aa11", 1000L);
        // Power lost after the earlier model was set aside and before the new one was in place.
        assertTrue(new File(root, "model").renameTo(new File(root, "model.previous")));
        assertTrue(new File(root, "model.incoming/am").mkdirs());

        VoiceModelStore restarted = new VoiceModelStore(root);
        restarted.recover();
        assertTrue(restarted.installed());
        assertEquals(Arrays.asList("model", "model.json"), Arrays.asList(sorted(root)));
    }

    private static String[] sorted(File directory) {
        String[] names = directory.list();
        Arrays.sort(names);
        return names;
    }
}
