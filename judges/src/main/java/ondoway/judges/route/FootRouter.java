package ondoway.judges.route;

import com.graphhopper.GHRequest;
import com.graphhopper.GHResponse;
import com.graphhopper.GraphHopper;
import com.graphhopper.ResponsePath;
import com.graphhopper.routing.ev.RoadClass;
import com.graphhopper.routing.ev.RoadEnvironment;
import com.graphhopper.util.DistanceCalcEarth;
import com.graphhopper.util.Parameters;
import com.graphhopper.util.PointList;
import com.graphhopper.util.details.PathDetail;
import com.graphhopper.util.exceptions.ConnectionNotFoundException;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import ondoway.judges.geo.LatLon;

/**
 * Routes walks on one city's OpenStreetMap graph with GraphHopper.
 *
 * <p>No walk uses a ferry line. A step-free walk uses no stair. A walk is "not
 * found" only when GraphHopper finds no path between the two attached points;
 * every other GraphHopper error is a tool failure and raises
 * {@link RouteFailureException}.
 */
public final class FootRouter {

    // One graph per city per process: a graph takes gigabytes of memory.
    private static final Map<String, FootRouter> OPEN = new HashMap<>();

    private static final List<String> EDGE_DETAILS = List.of(RoadClass.KEY, RoadEnvironment.KEY);

    private final String city;
    private final GraphHopper hopper;

    private FootRouter(String city, GraphHopper hopper) {
        this.city = city;
        this.hopper = hopper;
    }

    /** The router for a city in cities.json, building its graph on first use. */
    public static synchronized FootRouter open(String city) {
        return OPEN.computeIfAbsent(city, FootRouter::load);
    }

    private static FootRouter load(String city) {
        JudgeFiles files = JudgeFiles.workingDirectory();
        CityMap map = CityList.find(files, city);
        return new FootRouter(city, FootGraph.open(map, files, JudgeSettings.read(files)));
    }

    /** The walk from one point to another, around every stair when stepFree is true. */
    public Walk walk(LatLon from, LatLon to, boolean stepFree) {
        GHResponse response = hopper.route(request(from, to, stepFree));
        List<Throwable> errors = response.getErrors();
        if (!errors.stream().allMatch(e -> e instanceof ConnectionNotFoundException)) {
            throw new RouteFailureException(city + ": GraphHopper failed to route " + from + " to " + to
                    + ": " + errors.stream().map(Throwable::getMessage).collect(Collectors.joining("; ")),
                    errors.getFirst());
        }
        return readWalk(response.getBest(), from, to);
    }

    private static GHRequest request(LatLon from, LatLon to, boolean stepFree) {
        GHRequest request = new GHRequest(from.lat(), from.lon(), to.lat(), to.lon())
                .setProfile(WalkingRules.PROFILE)
                .setPathDetails(EDGE_DETAILS)
                .putHint(Parameters.Routing.INSTRUCTIONS, false)
                // GraphHopper computes path details only while it computes the path's points.
                .putHint(Parameters.Routing.CALC_POINTS, true);
        if (stepFree) {
            request.setCustomModel(WalkingRules.stepFree());
        }
        return request;
    }

    private static Walk readWalk(ResponsePath path, LatLon from, LatLon to) {
        // GraphHopper withholds the attached points of a path it did not find.
        if (path.hasErrors()) {
            return Walk.notFound();
        }
        // The waypoints are the points the asked points were attached to.
        PointList attached = path.getWaypoints();
        double fromSnap = metresBetween(from, attached.getLat(0), attached.getLon(0));
        double toSnap = metresBetween(to, attached.getLat(1), attached.getLon(1));
        return new Walk(true, path.getDistance(), path.getTime(), fromSnap, toSnap,
                edgeValues(path, RoadClass.KEY), edgeValues(path, RoadEnvironment.KEY));
    }

    /** The names a path detail takes along the path, as upper-case enum names. */
    private static Set<String> edgeValues(ResponsePath path, String detail) {
        List<PathDetail> spans = path.getPathDetails().get(detail);
        if (spans == null) {
            throw new RouteFailureException("GraphHopper returned no " + detail + " for the walk");
        }
        // GraphHopper writes these enum values in lower case.
        return spans.stream()
                .map(span -> span.getValue().toString().toUpperCase(Locale.ROOT))
                .collect(Collectors.toSet());
    }

    private static double metresBetween(LatLon asked, double lat, double lon) {
        return DistanceCalcEarth.DIST_EARTH.calcDist(asked.lat(), asked.lon(), lat, lon);
    }
}
