import java.io.InputStream;
import java.security.CodeSigner;
import java.security.MessageDigest;
import java.util.Enumeration;
import java.util.TreeSet;
import java.util.jar.JarEntry;
import java.util.jar.JarFile;

/**
 * Prints the verified v1 signers of each APK, as stage.py check-signers reads
 * them: "PATH\tSHA256,SHA256" (each signer certificate's SHA-256), or
 * "PATH\tERROR reason". Like Android 6's PackageParser, every entry outside
 * META-INF/ must be signed by the same signers, and AndroidManifest.xml must
 * be among them. Runs in the build container, so it keeps to Java 7.
 */
public final class ApkSigners {
    public static void main(String[] args) throws Exception {
        MessageDigest sha256 = MessageDigest.getInstance("SHA-256");
        for (String path : args) {
            String result;
            try {
                result = signers(path, sha256);
            } catch (Exception e) {
                result = "ERROR " + e.toString().replace('\t', ' ').replace('\n', ' ');
            }
            System.out.println(path + "\t" + result);
        }
    }

    private static String signers(String path, MessageDigest sha256) throws Exception {
        JarFile jar = new JarFile(path, true);
        try {
            TreeSet<String> first = null;
            boolean manifest = false;
            byte[] buffer = new byte[65536];
            for (Enumeration<JarEntry> entries = jar.entries(); entries.hasMoreElements();) {
                JarEntry entry = entries.nextElement();
                String name = entry.getName();
                if (entry.isDirectory() || name.startsWith("META-INF/")) {
                    continue;
                }
                // An entry's signers are known once it has been read to its end.
                InputStream in = jar.getInputStream(entry);
                try {
                    while (in.read(buffer) != -1) {
                    }
                } finally {
                    in.close();
                }
                CodeSigner[] codeSigners = entry.getCodeSigners();
                if (codeSigners == null || codeSigners.length == 0) {
                    throw new Exception("unsigned entry " + name);
                }
                TreeSet<String> set = new TreeSet<String>();
                for (CodeSigner signer : codeSigners) {
                    byte[] cert = signer.getSignerCertPath().getCertificates().get(0).getEncoded();
                    set.add(hex(sha256.digest(cert)));
                }
                if (first == null) {
                    first = set;
                } else if (!first.equals(set)) {
                    throw new Exception("other signers at " + name);
                }
                if (name.equals("AndroidManifest.xml")) {
                    manifest = true;
                }
            }
            if (!manifest) {
                throw new Exception("no AndroidManifest.xml");
            }
            StringBuilder out = new StringBuilder();
            for (String signer : first) {
                out.append(out.length() == 0 ? "" : ",").append(signer);
            }
            return out.toString();
        } finally {
            jar.close();
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder();
        for (byte b : bytes) {
            out.append(String.format("%02x", b & 0xff));
        }
        return out.toString();
    }
}
