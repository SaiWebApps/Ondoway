package ondoway.judges.probe;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Duration;
import java.util.Optional;
import java.util.Random;
import ondoway.judges.verdict.EndMode;
import ondoway.judges.verdict.SolverIndecisiveException;
import ondoway.judges.verdict.WalkSolver;
import org.junit.jupiter.api.Test;

/**
 * Milestone M4: OR-Tools CP-SAT loads on this machine and tells the three tour
 * shapes apart. Every walking-time table here is built by hand, so the right
 * answer is known by construction.
 */
class SolverProbeTest {

    private static final Duration LIMIT = Duration.ofSeconds(30);

    /**
     * Four stops on a straight street, 60 s apart, start at stop 0. An open walk
     * ends at the far end (0,1,2,3). A walk that must end at stop 1 goes out and
     * back (0,3,2,1 or 0,2,3,1 cost the same; the solver returns one of them).
     */
    private static long[][] street() {
        long[][] t = new long[4][4];
        for (int i = 0; i < 4; i++) {
            for (int j = 0; j < 4; j++) {
                t[i][j] = 60L * Math.abs(i - j);
            }
        }
        return t;
    }

    @Test
    void openWalkEndsAtTheFarEnd() {
        Optional<int[]> order = WalkSolver.shortestOrder(street(), 0, EndMode.OPEN, -1, 10_000, LIMIT);
        assertArrayEquals(new int[] {0, 1, 2, 3}, order.orElseThrow());
    }

    @Test
    void fixedEndWalkFinishesAtTheNamedStop() {
        int[] order = WalkSolver.shortestOrder(street(), 0, EndMode.FIXED_END, 1, 10_000, LIMIT)
                .orElseThrow();
        assertArrayEquals(new int[] {0, 1, 2, 3}, java.util.Arrays.stream(order).sorted().toArray(),
                "the walk must visit every stop exactly once");
        assertTrue(order[0] == 0 && order[order.length - 1] == 1,
                "the walk must start at stop 0 and end at stop 1");
        long seconds = 0;
        for (int i = 0; i + 1 < order.length; i++) {
            seconds += street()[order[i]][order[i + 1]];
        }
        assertTrue(seconds == 300, "the shortest walk ending at stop 1 takes 300 s, not " + seconds);
    }

    @Test
    void roundTripCountsTheWalkHome() {
        // Open: 180 s. Round trip: out 180 s and home 180 s = 360 s. A 300 s budget
        // allows the open walk and refuses the round trip.
        assertTrue(WalkSolver.shortestOrder(street(), 0, EndMode.OPEN, -1, 300, LIMIT).isPresent());
        assertTrue(WalkSolver.shortestOrder(street(), 0, EndMode.ROUND_TRIP, -1, 300, LIMIT).isEmpty());
    }

    @Test
    void roundTripFitsWhenTheBudgetCoversTheWalkHome() {
        // A round trip's order starts at the start stop; the walk home is implied.
        int[] order = WalkSolver.shortestOrder(street(), 0, EndMode.ROUND_TRIP, -1, 360, LIMIT)
                .orElseThrow();
        assertArrayEquals(new int[] {0, 1, 2, 3}, java.util.Arrays.stream(order).sorted().toArray(),
                "the round trip must visit every stop exactly once");
        long seconds = street()[order[order.length - 1]][order[0]];
        for (int i = 0; i + 1 < order.length; i++) {
            seconds += street()[order[i]][order[i + 1]];
        }
        assertTrue(order[0] == 0 && seconds == 360,
                "the round trip starts at stop 0 and takes 360 s with the walk home, not " + seconds);
    }

    @Test
    void noOrderFitsAnImpossibleBudget() {
        assertTrue(WalkSolver.shortestOrder(street(), 0, EndMode.OPEN, -1, 179, LIMIT).isEmpty());
    }

    @Test
    void twentyFiveStopsFinishInsideTheLimit() {
        Random random = new Random(20261122L);
        int n = 25;
        double[][] xy = new double[n][2];
        for (int i = 0; i < n; i++) {
            xy[i][0] = random.nextDouble() * 3000;
            xy[i][1] = random.nextDouble() * 3000;
        }
        long[][] t = new long[n][n];
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < n; j++) {
                t[i][j] = Math.round(Math.hypot(xy[i][0] - xy[j][0], xy[i][1] - xy[j][1]));
            }
        }
        long started = System.nanoTime();
        Optional<int[]> order = WalkSolver.shortestOrder(t, 0, EndMode.OPEN, -1, 1_000_000, LIMIT);
        Duration took = Duration.ofNanos(System.nanoTime() - started);

        assertTrue(order.isPresent(), "25 stops with an unlimited budget must have an order");
        assertTrue(took.compareTo(LIMIT) <= 0, "the 25-stop solve took " + took);
    }

    @Test
    void anUndecidedSolveIsAnErrorNotAVerdict() {
        Random random = new Random(7L);
        int n = 25;
        long[][] t = new long[n][n];
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < n; j++) {
                t[i][j] = i == j ? 0 : 1 + random.nextInt(1000);
            }
        }
        assertThrows(SolverIndecisiveException.class,
                () -> WalkSolver.shortestOrder(t, 0, EndMode.OPEN, -1, 1_000_000, Duration.ZERO));
    }
}
