package ondoway.judges.verdict;

/**
 * The solver stopped without proving an answer. This is a tool failure, never a
 * verdict: it neither returns an order nor says that no order fits.
 */
public final class SolverIndecisiveException extends RuntimeException {

    public SolverIndecisiveException(String message) {
        super(message);
    }
}
