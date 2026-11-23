package ondoway.judges.probe;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import ondoway.judges.route.FootRouter;
import ondoway.judges.route.Walk;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.FieldSource;

/**
 * Milestone M2: the distance from a stop to the path it snaps to can tell a stop
 * in the water from a stop on land. GraphHopper snaps a far stop onto a path
 * instead of failing, so this distance is what lets a judge answer NO.
 *
 * The land points come from one Wikidata query recorded in land-points.json, so
 * nobody picks them by hand. Every water point in water-points.json carries the
 * query that proved it lies inside a water area, and that query's answer. Ferry
 * lines are blocked, so a water stop cannot snap onto a ferry.
 */
class SnapProbeTest {

    static final List<String> CITIES = Fixtures.LAUNCH_CITIES;

    @ParameterizedTest
    @FieldSource("CITIES")
    void waterStopsSnapFartherThanEveryLandStop(String city) {
        FootRouter router = FootRouter.open(city);
        List<Fixtures.Point> land = Fixtures.points("land-points.json", city);
        List<Fixtures.Point> water = Fixtures.points("water-points.json", city);
        assertTrue(land.size() >= 3, city + ": fewer than three land points prove nothing");

        // Every land point is measured: each one is the start of one walk and the
        // end of another, so its snap distance is read at least once.
        double farthestLand = 0;
        for (int i = 0; i < land.size(); i++) {
            Fixtures.Point next = land.get((i + 1) % land.size());
            Walk walk = router.walk(land.get(i).at(), next.at(), false);
            assertTrue(walk.found(), city + ": no walk from land point " + land.get(i).name());
            assertFalse(walk.roadEnvironments().contains("FERRY"), city + ": a land walk rides a ferry");
            farthestLand = Math.max(farthestLand,
                    Math.max(walk.fromSnapMeters(), walk.toSnapMeters()));
        }
        Fixtures.Point anchor = land.get(0);
        for (Fixtures.Point stop : water) {
            Walk walk = router.walk(anchor.at(), stop.at(), false);
            assertTrue(walk.found(), city + ": no walk to water stop " + stop.name());
            assertFalse(walk.roadEnvironments().contains("FERRY"),
                    city + ": the walk to water stop " + stop.name() + " rides a ferry");
            System.out.println(city + ": water stop " + stop.name() + " snaps "
                    + walk.toSnapMeters() + " m; farthest land stop " + farthestLand + " m");
            assertTrue(walk.toSnapMeters() > farthestLand,
                    city + ": water stop " + stop.name() + " snapped " + walk.toSnapMeters()
                            + " m away, no farther than a land stop (" + farthestLand + " m)");
        }
    }
}
