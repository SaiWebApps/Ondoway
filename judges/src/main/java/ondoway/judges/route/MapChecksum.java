package ondoway.judges.route;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/** Proves that a map file on disk is the map the city list pins. */
final class MapChecksum {

    private static final int CHUNK_BYTES = 1 << 20;

    private MapChecksum() {}

    static void require(CityMap map) {
        if (!Files.isRegularFile(map.mapFile())) {
            throw new RouteFailureException(map.city() + ": the map " + map.mapFile() + " is not on disk");
        }
        String actual = md5Of(map.mapFile());
        if (!actual.equals(map.mapMd5())) {
            throw new RouteFailureException(map.city() + ": " + map.mapFile().getFileName()
                    + " has MD5 " + actual + ", but the city list pins " + map.mapMd5());
        }
    }

    private static String md5Of(Path file) {
        try (InputStream in = Files.newInputStream(file)) {
            MessageDigest digest = MessageDigest.getInstance("MD5");
            byte[] chunk = new byte[CHUNK_BYTES];
            for (int read = in.read(chunk); read >= 0; read = in.read(chunk)) {
                digest.update(chunk, 0, read);
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch (IOException e) {
            throw new RouteFailureException("cannot read " + file, e);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("every Java runtime provides MD5", e);
        }
    }
}
