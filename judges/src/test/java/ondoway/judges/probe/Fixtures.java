package ondoway.judges.probe;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.ArrayList;
import java.util.List;
import ondoway.judges.geo.LatLon;

/**
 * Reads the probe fixtures under src/test/resources. Every point in a fixture
 * names the source that proves where it is, so no test point is a guess.
 */
final class Fixtures {

    /** The launch cities every probe runs for. */
    static final List<String> LAUNCH_CITIES = List.of("paris", "new-york");

    private static final ObjectMapper JSON = new ObjectMapper();

    private Fixtures() {}

    record Point(String name, LatLon at, String source) {}

    static JsonNode read(String resource) {
        try (InputStream in = Fixtures.class.getResourceAsStream("/" + resource)) {
            if (in == null) {
                throw new IllegalStateException("missing test fixture " + resource);
            }
            return JSON.readTree(in);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /** The points listed for one city in a fixture of the shape {city: [point, ...]}. */
    static List<Point> points(String resource, String city) {
        JsonNode list = read(resource).path(city);
        if (!list.isArray() || list.isEmpty()) {
            throw new IllegalStateException(resource + " lists no points for " + city);
        }
        List<Point> points = new ArrayList<>();
        for (JsonNode p : list) {
            String source = p.path("source").asText("");
            if (source.isBlank()) {
                throw new IllegalStateException(
                        resource + ": point " + p.path("name").asText() + " names no source");
            }
            points.add(new Point(
                    p.path("name").asText(),
                    new LatLon(p.path("lat").asDouble(), p.path("lon").asDouble()),
                    source));
        }
        return points;
    }
}
