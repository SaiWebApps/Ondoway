package ondoway.judges.probe;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.List;
import ondoway.judges.geo.LatLon;
import ondoway.judges.route.FootRouter;
import ondoway.judges.route.Walk;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.FieldSource;

/**
 * Milestone M3: a step-free walk really avoids steps. A longer route proves
 * nothing, so the test reads the road class of every edge the route uses.
 *
 * steps-pairs.json gives each city two fixtures, each with its source:
 *   "detour": a pair whose ordinary walk uses steps and a step-free way around exists;
 *   "stairs_only": a pair whose only link is a stair.
 */
class StepsProbeTest {

    static final List<String> CITIES = Fixtures.LAUNCH_CITIES;

    private static LatLon at(JsonNode p) {
        return new LatLon(p.path("lat").asDouble(), p.path("lon").asDouble());
    }

    @ParameterizedTest
    @FieldSource("CITIES")
    void stepFreeWalkUsesNoSteps(String city) {
        JsonNode detour = Fixtures.read("steps-pairs.json").path(city).path("detour");
        assertFalse(detour.path("source").asText("").isBlank(), city + ": detour names no source");
        FootRouter router = FootRouter.open(city);

        Walk ordinary = router.walk(at(detour.path("from")), at(detour.path("to")), false);
        Walk stepFree = router.walk(at(detour.path("from")), at(detour.path("to")), true);

        assertTrue(ordinary.roadClasses().contains("STEPS"),
                city + ": the ordinary walk uses no steps, so this pair proves nothing");
        assertTrue(stepFree.found(), city + ": no step-free walk found around the steps");
        assertFalse(stepFree.roadClasses().contains("STEPS"),
                city + ": the step-free walk still uses steps");
    }

    @ParameterizedTest
    @FieldSource("CITIES")
    void stairsOnlyLinkHasNoStepFreeWalk(String city) {
        JsonNode stairs = Fixtures.read("steps-pairs.json").path(city).path("stairs_only");
        assertFalse(stairs.path("source").asText("").isBlank(),
                city + ": stairs_only names no source");
        FootRouter router = FootRouter.open(city);
        Walk ordinary = router.walk(at(stairs.path("from")), at(stairs.path("to")), false);
        Walk stepFree = router.walk(at(stairs.path("from")), at(stairs.path("to")), true);

        // Without these two checks, a point off the map or a rejected rule would
        // also give "no walk found", and the test would pass with no stair involved.
        assertTrue(ordinary.found(), city + ": the ordinary walk over the stair was not found");
        assertTrue(ordinary.roadClasses().contains("STEPS"),
                city + ": the ordinary walk uses no steps, so this pair is not stair-only");
        assertFalse(stepFree.found(), city + ": a step-free walk was found over a stair-only link");
    }
}
