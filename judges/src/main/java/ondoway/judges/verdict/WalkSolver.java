package ondoway.judges.verdict;

import com.google.ortools.Loader;
import com.google.ortools.sat.BoolVar;
import com.google.ortools.sat.CircuitConstraint;
import com.google.ortools.sat.CpModel;
import com.google.ortools.sat.CpSolver;
import com.google.ortools.sat.CpSolverStatus;
import com.google.ortools.sat.LinearExpr;
import com.google.ortools.sat.LinearExprBuilder;
import java.time.Duration;
import java.util.Optional;

/**
 * Finds the shortest order that visits every stop once and fits a time budget,
 * using the OR-Tools CP-SAT solver.
 *
 * <p>CP-SAT's circuit constraint asks for one closed loop through every node. Each
 * walk becomes such a loop through one extra finish node: the finish node leads
 * only to the start stop, and only the stops the walk may end at lead to the
 * finish node. The arc into the finish node costs whatever the end mode adds after
 * the last stop, so one model serves every end mode.
 */
public final class WalkSolver {

    /** The value a caller passes as the fixed end when the end mode names no end stop. */
    public static final int NO_FIXED_END = -1;

    static {
        Loader.loadNativeLibraries();
    }

    private WalkSolver() {}

    /**
     * Returns the shortest order that starts at {@code start}, visits every stop
     * once, ends as {@code endMode} says and takes at most {@code budgetSeconds}.
     * A round trip's order lists the start once, first; the walk home is implied.
     *
     * @param seconds the walking seconds from stop {@code i} to stop {@code j} at {@code [i][j]}
     * @param fixedEnd the stop a {@link EndMode#FIXED_END} walk ends at, otherwise {@link #NO_FIXED_END}
     * @return the order, or empty when the solver proves that no order fits the budget
     * @throws SolverIndecisiveException when the solve stops without a proven answer
     */
    public static Optional<int[]> shortestOrder(
            long[][] seconds, int start, EndMode endMode, int fixedEnd, long budgetSeconds, Duration timeLimit) {
        checkInput(seconds, start, endMode, fixedEnd, timeLimit);

        CpModel model = new CpModel();
        BoolVar[][] arcs = addWalkCircuit(model, seconds.length, start, endMode, fixedEnd);
        LinearExpr walkSeconds = walkSeconds(arcs, seconds, start, endMode);
        model.addLessOrEqual(walkSeconds, budgetSeconds);
        model.minimize(walkSeconds);

        CpSolver solver = new CpSolver();
        solver.getParameters().setMaxTimeInSeconds(inSeconds(timeLimit));
        CpSolverStatus status = solver.solve(model);
        return switch (status) {
            case OPTIMAL -> Optional.of(readOrder(solver, arcs, start));
            case INFEASIBLE -> Optional.empty();
            case FEASIBLE, UNKNOWN -> throw new SolverIndecisiveException(
                    "The solver stopped with status " + status + " after " + timeLimit
                            + ", so it proved neither the shortest order nor that no order fits.");
            case MODEL_INVALID, UNRECOGNIZED -> throw new IllegalStateException(
                    "The solver refused the walk model with status " + status + ": " + model.validate());
        };
    }

    /** Refuses a table, start, end or time limit that does not describe a walk. */
    private static void checkInput(
            long[][] seconds, int start, EndMode endMode, int fixedEnd, Duration timeLimit) {
        int stops = seconds.length;
        if (stops == 0) {
            throw new IllegalArgumentException("The walking-time table has no stops.");
        }
        for (int i = 0; i < stops; i++) {
            if (seconds[i].length != stops) {
                throw new IllegalArgumentException("Row " + i + " of the walking-time table has "
                        + seconds[i].length + " entries; the table has " + stops + " stops.");
            }
            for (int j = 0; j < stops; j++) {
                if (seconds[i][j] < 0) {
                    throw new IllegalArgumentException(
                            "The walk from stop " + i + " to stop " + j + " takes negative time.");
                }
            }
        }
        if (start < 0 || start >= stops) {
            throw new IllegalArgumentException("The start stop " + start + " is not in the table.");
        }
        if (endMode == EndMode.FIXED_END) {
            if (fixedEnd < 0 || fixedEnd >= stops) {
                throw new IllegalArgumentException("The fixed end stop " + fixedEnd + " is not in the table.");
            }
            if (fixedEnd == start && stops > 1) {
                throw new IllegalArgumentException(
                        "A walk that ends at its own start is a round trip, not a fixed-end walk.");
            }
        } else if (fixedEnd != NO_FIXED_END) {
            throw new IllegalArgumentException(
                    "A " + endMode + " walk names no fixed end, but stop " + fixedEnd + " was given.");
        }
        if (timeLimit.isNegative()) {
            throw new IllegalArgumentException("The solve time limit " + timeLimit + " is negative.");
        }
    }

    /**
     * Adds the circuit over the stops plus the finish node, whose index is the stop
     * count. Returns each allowed arc's literal at {@code [tail][head]}; a forbidden
     * arc is null.
     */
    private static BoolVar[][] addWalkCircuit(
            CpModel model, int stops, int start, EndMode endMode, int fixedEnd) {
        int finish = stops;
        BoolVar[][] arcs = new BoolVar[stops + 1][stops + 1];
        CircuitConstraint circuit = model.addCircuit();

        // Nothing walks into the start stop except the finish node closing the loop.
        arcs[finish][start] = model.newBoolVar("finish->" + start);
        circuit.addArc(finish, start, arcs[finish][start]);
        for (int tail = 0; tail < stops; tail++) {
            for (int head = 0; head < stops; head++) {
                if (tail != head && head != start) {
                    arcs[tail][head] = model.newBoolVar(tail + "->" + head);
                    circuit.addArc(tail, head, arcs[tail][head]);
                }
            }
            if (endMode.mayFinishAt(tail, fixedEnd)) {
                arcs[tail][finish] = model.newBoolVar(tail + "->finish");
                circuit.addArc(tail, finish, arcs[tail][finish]);
            }
        }
        return arcs;
    }

    /** The walk's total seconds: every chosen leg, plus what the end mode adds after the last stop. */
    private static LinearExpr walkSeconds(BoolVar[][] arcs, long[][] seconds, int start, EndMode endMode) {
        int finish = seconds.length;
        LinearExprBuilder total = LinearExpr.newBuilder();
        for (int tail = 0; tail < seconds.length; tail++) {
            for (int head = 0; head < seconds.length; head++) {
                if (arcs[tail][head] != null) {
                    total.addTerm(arcs[tail][head], seconds[tail][head]);
                }
            }
            if (arcs[tail][finish] != null) {
                total.addTerm(arcs[tail][finish], endMode.secondsAfterLastStop(seconds, tail, start));
            }
        }
        return total.build();
    }

    /** Follows the chosen arcs from the start stop until the walk reaches the finish node. */
    private static int[] readOrder(CpSolver solver, BoolVar[][] arcs, int start) {
        int finish = arcs.length - 1;
        int[] order = new int[finish];
        int stop = start;
        for (int position = 0; stop != finish; position++) {
            order[position] = stop;
            stop = chosenNext(solver, arcs[stop]);
        }
        return order;
    }

    /** The head of the one chosen arc among {@code arcsFromStop}. */
    private static int chosenNext(CpSolver solver, BoolVar[] arcsFromStop) {
        for (int head = 0; head < arcsFromStop.length; head++) {
            if (arcsFromStop[head] != null && solver.booleanValue(arcsFromStop[head])) {
                return head;
            }
        }
        throw new IllegalStateException("The solver's circuit leaves a stop by no arc.");
    }

    private static double inSeconds(Duration duration) {
        return duration.getSeconds() + duration.getNano() / 1e9;
    }
}
