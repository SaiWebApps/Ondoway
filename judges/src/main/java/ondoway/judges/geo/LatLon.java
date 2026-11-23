package ondoway.judges.geo;

/** A point on the earth: latitude then longitude, both in WGS84 decimal degrees. */
public record LatLon(double lat, double lon) {}
