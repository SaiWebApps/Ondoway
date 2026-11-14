"""The OSM (Overpass) source connector.

PURE, like every connector: ``consult_url`` names the Overpass endpoint + the
Overpass QL it wants POST-ed as the ``data`` param, and ``parse`` maps a raw
Overpass JSON payload to typed ``PoiCandidate``s. Neither touches the network or
the filesystem — the fetch happens only in ``base.run_connector`` (the allowlist
door), which this module deliberately does NOT import.

THE GOTCHA this connector exists to get right: an Overpass ``node`` carries its
coordinates TOP-LEVEL (``lat``/``lon``), whereas a ``way``/``relation`` carries
them under a ``center`` block (present because the query ends ``out center;``).
``parse`` reads each from the correct place so a named POI from either type lands
with real coordinates.
"""

from __future__ import annotations

from src.onboard.models import CityContext, ConnectorResult, PoiCandidate

# Overpass interpreter endpoint (POST-ed the QL as the ``data`` param).
_ENDPOINT = "https://overpass-api.de/api/interpreter"

# OSM tag keys worth surfacing as tour-POI candidates.
_TAG_KEYS = ("tourism", "historic")


class OsmSource:
    """Consults the Overpass API for tourism/historic OSM features in the city
    bbox and turns each named node/way/relation into a ``PoiCandidate``."""

    slug = "osm"

    def consult_url(self, ctx: CityContext) -> tuple[str, dict | None]:
        """The Overpass endpoint + the QL to POST as ``data``. PURE.

        Overpass bbox order is ``(south,west,north,east)`` =
        ``(min_lat, min_lon, max_lat, max_lon)``. The query ends with
        ``out center;`` so ways/relations come back with a ``center`` block.
        """
        min_lat, max_lat, min_lon, max_lon = ctx.bbox
        bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"  # (south,west,north,east)
        selectors = "\n".join(
            f'  node["{key}"]({bbox});\n'
            f'  way["{key}"]({bbox});\n'
            f'  relation["{key}"]({bbox});'
            for key in _TAG_KEYS
        )
        ql = f"[out:json][timeout:60];\n(\n{selectors}\n);\nout center;"
        return (_ENDPOINT, {"data": ql})

    def parse(self, payload: dict, ctx: CityContext) -> ConnectorResult:
        """Map an Overpass JSON payload to named ``PoiCandidate``s. PURE.

        Elements without a ``tags.name`` are skipped. Node coords are read
        top-level; way/relation coords are read from ``center``.
        """
        candidates: list[PoiCandidate] = []
        for element in payload.get("elements", []):
            tags = element.get("tags", {})
            name = tags.get("name")
            if not name:
                continue  # un-named feature — nothing to surface

            etype = element.get("type")
            if etype == "node":
                # NODE: coordinates live at the TOP LEVEL of the element.
                latitude = element.get("lat")
                longitude = element.get("lon")
            elif etype in ("way", "relation"):
                # WAY/RELATION: coordinates live under the ``center`` block.
                center = element.get("center", {})
                latitude = center.get("lat")
                longitude = center.get("lon")
            else:
                latitude = longitude = None

            osm_id = element.get("id")
            candidates.append(
                PoiCandidate(
                    name=name,
                    source=self.slug,
                    source_url=f"https://www.openstreetmap.org/{etype}/{osm_id}",
                    latitude=latitude,
                    longitude=longitude,
                    # STOP discarding tags: the assemble filter reads these
                    # curation signals to keep real POIs (a museum/monument, or
                    # anything OSM cross-references to Wikidata/Wikipedia) and
                    # cut noise. Absent tags degrade to None via tags.get.
                    meta={
                        "osm_type": etype,
                        "osm_id": osm_id,
                        "tourism": tags.get("tourism"),
                        "historic": tags.get("historic"),
                        "osm_wikidata": tags.get("wikidata"),
                        "osm_wikipedia": tags.get("wikipedia"),
                    },
                )
            )
        return ConnectorResult(candidates=candidates)
