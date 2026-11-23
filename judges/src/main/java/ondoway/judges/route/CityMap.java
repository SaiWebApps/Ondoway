package ondoway.judges.route;

import java.nio.file.Path;

/** One city's street map: the file on disk and the MD5 checksum that pins it. */
public record CityMap(String city, Path mapFile, String mapMd5) {}
