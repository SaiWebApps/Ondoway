package ondoway.judges.route;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.util.regex.Pattern;

/**
 * Reads the judges' city list, cities.json. Each city is one entry under its key,
 * so adding a city is one new entry and no code change.
 */
public final class CityList {

    // A map file name is a bare name, so every map lies inside the maps folder.
    private static final Pattern FILE_NAME = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]*");
    private static final Pattern MD5 = Pattern.compile("[0-9a-f]{32}");

    private CityList() {}

    /** The map entry for one city, with its file name and checksum checked for shape. */
    public static CityMap find(JudgeFiles files, String city) {
        JsonNode entry = readList(files).path(city);
        if (!entry.isObject()) {
            throw new RouteFailureException(files.citiesFile() + " lists no city called " + city);
        }
        String mapFile = entry.path("map_file").asText("");
        String mapMd5 = entry.path("map_md5").asText("");
        if (!FILE_NAME.matcher(mapFile).matches()) {
            throw new RouteFailureException(city + ": map_file must be a bare file name");
        }
        if (!MD5.matcher(mapMd5).matches()) {
            throw new RouteFailureException(city + ": map_md5 must be 32 lowercase hex digits");
        }
        return new CityMap(city, files.mapsFolder().resolve(mapFile), mapMd5);
    }

    private static JsonNode readList(JudgeFiles files) {
        try {
            return new ObjectMapper().readTree(files.citiesFile().toFile());
        } catch (IOException e) {
            throw new RouteFailureException("cannot read " + files.citiesFile(), e);
        }
    }
}
