package ondoway.judges.verdict;

/** How a walk ends once it has visited every stop. */
public enum EndMode {

    /** The walk ends at whichever stop it reaches last, and nothing more is walked. */
    OPEN,

    /** The walk must reach the named end stop last. */
    FIXED_END,

    /** The walk returns to its start, and the walk home counts against the budget. */
    ROUND_TRIP;

    /** Whether a walk in this mode may reach {@code stop} last. */
    boolean mayFinishAt(int stop, int fixedEnd) {
        return this != FIXED_END || stop == fixedEnd;
    }

    /** The seconds this mode adds after the walk reaches {@code stop} last. */
    long secondsAfterLastStop(long[][] seconds, int stop, int start) {
        return this == ROUND_TRIP ? seconds[stop][start] : 0L;
    }
}
