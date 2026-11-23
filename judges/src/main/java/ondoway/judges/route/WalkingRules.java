package ondoway.judges.route;

import com.graphhopper.GraphHopper;
import com.graphhopper.config.Profile;
import com.graphhopper.json.Statement;
import com.graphhopper.routing.ev.DefaultImportRegistry;
import com.graphhopper.routing.ev.ImportRegistry;
import com.graphhopper.routing.weighting.custom.CustomModelParser;
import com.graphhopper.util.CustomModel;
import com.graphhopper.util.JsonFeatureCollection;
import java.util.List;

/**
 * The judges' walking rules, written as GraphHopper custom models.
 *
 * <p>The walking profile is GraphHopper's own foot model with one rule added: no
 * walk uses a ferry line. GraphHopper's foot access accepts ferry lines, and its
 * foot model never blocks them, so the judges block them here. The rule sits in
 * the profile itself, so it holds for every walk and also stops a point from
 * being attached to a ferry line.
 *
 * <p>The step-free rule is a per-request rule added on top of the profile. A
 * per-request rule works only with GraphHopper's flexible routing mode, so the
 * walking profile has no speed-up preparation.
 */
final class WalkingRules {

    static final String PROFILE = "judge_foot";

    // GraphHopper ships this foot model inside its own jar.
    private static final String GRAPHHOPPER_FOOT_MODEL = "foot.json";

    private WalkingRules() {}

    /** The walking profile: GraphHopper's foot model, with every ferry line blocked. */
    static Profile footProfile() {
        return new Profile(PROFILE).setCustomModel(CustomModel.merge(graphHopperFootModel(), noFerry()));
    }

    /** A fresh per-request rule that blocks every stair. */
    static CustomModel stepFree() {
        return new CustomModel().addToPriority(
                Statement.If("road_class == STEPS", Statement.Op.MULTIPLY, "0"));
    }

    /**
     * Every stored map value the walking profile reads. GraphHopper refuses to
     * build a graph whose profile reads a value that this list does not name.
     */
    static String storedValues() {
        ImportRegistry registry = new DefaultImportRegistry();
        return String.join(", ", CustomModelParser.findVariablesForEncodedValuesString(
                footProfile().getCustomModel(),
                name -> registry.createImportUnit(name) != null,
                name -> ""));
    }

    private static CustomModel noFerry() {
        return new CustomModel().addToPriority(
                Statement.If("road_environment == FERRY", Statement.Op.MULTIPLY, "0"));
    }

    private static CustomModel graphHopperFootModel() {
        Profile byFileName = new Profile(PROFILE);
        // A profile names its model either inline or by file, never both.
        byFileName.getHints().remove(CustomModel.KEY);
        byFileName.putHint("custom_model_files", List.of(GRAPHHOPPER_FOOT_MODEL));
        return GraphHopper.resolveCustomModelFiles("", List.of(byFileName), new JsonFeatureCollection())
                .getFirst()
                .getCustomModel();
    }
}
