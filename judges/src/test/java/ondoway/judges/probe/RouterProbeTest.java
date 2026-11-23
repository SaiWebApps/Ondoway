package ondoway.judges.probe;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import ondoway.judges.route.FootRouter;
import ondoway.judges.route.Walk;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.FieldSource;

/**
 * Milestone M1: GraphHopper builds each launch city's foot graph and routes a walk
 * in it. A ferry is not a walk, so no edge of a walk is a ferry line: GraphHopper's
 * foot rules accept ferry lines unless the judges' own walking rules block them.
 */
class RouterProbeTest {

    static final List<String> CITIES = Fixtures.LAUNCH_CITIES;

    @ParameterizedTest
    @FieldSource("CITIES")
    void routesOneWalkInEachLaunchCity(String city) {
        List<Fixtures.Point> land = Fixtures.points("land-points.json", city);
        Walk walk = FootRouter.open(city).walk(land.get(0).at(), land.get(1).at(), false);

        assertTrue(walk.found(), city + ": no walking route between the first two land points");
        assertTrue(walk.distanceMeters() > 0, city + ": the walk has no length");
        assertTrue(walk.timeMillis() > 0, city + ": the walk takes no time");
        assertFalse(walk.roadEnvironments().contains("FERRY"), city + ": the walk rides a ferry");
        System.out.println(city + ": " + walk.distanceMeters() + " m in " + walk.timeMillis() + " ms");
    }
}
