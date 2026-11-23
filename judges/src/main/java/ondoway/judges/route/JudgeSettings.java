package ondoway.judges.route;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;

/**
 * The judges' settings, read from settings.json. Every value a person may want to
 * change lives there, and every caller reads it through this class.
 */
public final class JudgeSettings {

    private final JsonNode root;
    private final String source;

    private JudgeSettings(JsonNode root, String source) {
        this.root = root;
        this.source = source;
    }

    public static JudgeSettings read(JudgeFiles files) {
        try {
            return new JudgeSettings(
                    new ObjectMapper().readTree(files.settingsFile().toFile()),
                    files.settingsFile().toString());
        } catch (IOException e) {
            throw new RouteFailureException("cannot read " + files.settingsFile(), e);
        }
    }

    /**
     * How many rings of map tiles the foot router searches around a point to find
     * the path it attaches the point to. One ring is the point's own tile; each
     * further ring is one tile wider on every side.
     */
    public int footRouterSnapSearchRings() {
        return positiveInt("foot_router", "snap_search_rings");
    }

    private int positiveInt(String section, String key) {
        JsonNode value = root.path(section).path(key);
        if (!value.canConvertToInt() || value.asInt() < 1) {
            throw new RouteFailureException(
                    source + ": " + section + "." + key + " must be a whole number of at least 1");
        }
        return value.asInt();
    }
}
