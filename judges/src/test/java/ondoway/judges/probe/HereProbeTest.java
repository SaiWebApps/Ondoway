package ondoway.judges.probe;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import ondoway.judges.route.HereQuotaException;
import ondoway.judges.route.HereRouter;
import ondoway.judges.route.Walk;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.FieldSource;

/**
 * Milestone M5: HERE's walking routes, on a map drawn apart from OpenStreetMap,
 * answer in every launch city. The key comes from HERE_API_KEY or judges/.env.local.
 *
 * HERE charges above its free monthly allowance, so the client counts every call
 * in a file that outlives the run, and refuses a call before making it once the
 * month's count reaches the cap in the judges' settings.
 */
class HereProbeTest {

    static final List<String> CITIES = Fixtures.LAUNCH_CITIES;

    @ParameterizedTest
    @FieldSource("CITIES")
    void routesOneWalkInEachLaunchCity(String city) {
        List<Fixtures.Point> land = Fixtures.points("land-points.json", city);
        HereRouter here = HereRouter.fromEnvironment();
        Walk walk = here.walk(land.get(0).at(), land.get(1).at());

        assertTrue(walk.found(), city + ": HERE found no walking route");
        assertTrue(walk.distanceMeters() > 0, city + ": HERE's walk has no length");
        assertTrue(walk.timeMillis() > 0, city + ": HERE's walk takes no time");
        System.out.println("HERE calls this month: " + here.callsThisMonth()
                + " of a cap of " + here.monthlyCap());
    }

    @Test
    void aCallPastTheCapIsRefusedBeforeItIsMade() {
        List<Fixtures.Point> land = Fixtures.points("land-points.json", "paris");
        HereRouter capped = HereRouter.fromEnvironment().withMonthlyCap(0);
        long before = capped.callsThisMonth();

        assertThrows(HereQuotaException.class,
                () -> capped.walk(land.get(0).at(), land.get(1).at()));
        assertEquals(before, capped.callsThisMonth(), "a refused call must not be counted or sent");
    }
}
