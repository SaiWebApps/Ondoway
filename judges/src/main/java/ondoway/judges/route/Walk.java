package ondoway.judges.route;

import java.util.Set;

/**
 * One walk between two points, as a router reports it.
 *
 * <p>A snap distance is how far, in metres, an asked point sits from the path the
 * router attached it to. Road classes and road environments are upper-case names
 * such as STEPS or FERRY, one entry for each kind of edge the walk uses.
 *
 * <p>A walk that was not found has no length, no time and no edges, and its snap
 * distances are unknown (NaN).
 */
public record Walk(
        boolean found,
        double distanceMeters,
        long timeMillis,
        double fromSnapMeters,
        double toSnapMeters,
        Set<String> roadClasses,
        Set<String> roadEnvironments) {

    public Walk {
        roadClasses = Set.copyOf(roadClasses);
        roadEnvironments = Set.copyOf(roadEnvironments);
    }

    /** A walk the router searched for and did not find. */
    public static Walk notFound() {
        return new Walk(false, 0, 0, Double.NaN, Double.NaN, Set.of(), Set.of());
    }
}
