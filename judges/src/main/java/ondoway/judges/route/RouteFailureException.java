package ondoway.judges.route;

/**
 * A routing tool failed, so it gives no answer about the walk. A judge reports
 * this as an error and never turns it into a verdict.
 */
public class RouteFailureException extends RuntimeException {

    public RouteFailureException(String message) {
        super(message);
    }

    public RouteFailureException(String message, Throwable cause) {
        super(message, cause);
    }
}
