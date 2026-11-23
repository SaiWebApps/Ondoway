package ondoway.judges.route;

import com.graphhopper.GraphHopper;
import com.graphhopper.GraphHopperConfig;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;

/**
 * Builds a city's walking graph once and loads it on every later run.
 *
 * <p>A graph's folder is named after the MD5 checksum of the map it was built
 * from, so a graph is only ever loaded for the very map that made it; a new map
 * has a new checksum and gets a graph of its own. A graph is built in a
 * side folder and moved into place only when it is whole, so a build that stops
 * half way never passes for a finished graph.
 */
final class FootGraph {

    private static final String BUILDING_SUFFIX = ".building";

    private FootGraph() {}

    /** The city's walking graph, built first if it is not on disk yet. */
    static GraphHopper open(CityMap map, JudgeFiles files, JudgeSettings settings) {
        Path graph = files.graphsFolder().resolve(map.mapMd5());
        if (!Files.isDirectory(graph)) {
            build(map, graph, settings);
        }
        return load(graph, settings);
    }

    private static void build(CityMap map, Path graph, JudgeSettings settings) {
        MapChecksum.require(map);
        Path building = graph.resolveSibling(graph.getFileName() + BUILDING_SUFFIX);
        deleteFolder(building);
        try {
            Files.createDirectories(building);
        } catch (IOException e) {
            throw new RouteFailureException("cannot create the graph folder " + building, e);
        }
        GraphHopper hopper = new GraphHopper();
        hopper.init(config(building, settings).putObject("datareader.file", map.mapFile().toString()));
        hopper.importAndClose();
        try {
            Files.move(building, graph, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            throw new RouteFailureException("cannot move the built graph into " + graph, e);
        }
    }

    private static GraphHopper load(Path graph, JudgeSettings settings) {
        GraphHopper hopper = new GraphHopper();
        hopper.init(config(graph, settings));
        if (!hopper.load()) {
            throw new RouteFailureException("the graph folder " + graph
                    + " holds no finished graph; delete it so the next run builds it again");
        }
        return hopper;
    }

    private static GraphHopperConfig config(Path location, JudgeSettings settings) {
        return new GraphHopperConfig()
                .putObject("graph.location", location.toString())
                .putObject("graph.encoded_values", WalkingRules.storedValues())
                // The import skips no kind of road, so the walking rules alone decide
                // which roads a walk may use.
                .putObject("import.osm.ignored_highways", "")
                // Read each time a graph loads, so changing it needs no rebuild.
                .putObject("index.max_region_search", settings.footRouterSnapSearchRings())
                .setProfiles(List.of(WalkingRules.footProfile()));
    }

    private static void deleteFolder(Path folder) {
        if (!Files.exists(folder)) {
            return;
        }
        try (Stream<Path> paths = Files.walk(folder)) {
            for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
                Files.delete(path);
            }
        } catch (IOException e) {
            throw new RouteFailureException("cannot clear the unfinished graph in " + folder, e);
        }
    }
}
