package ondoway.judges.route;

import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Where the judges keep their files. The root is the judges folder, the one that
 * holds cities.json; every other path is fixed relative to it.
 */
public record JudgeFiles(Path root) {

    /** The judges folder is the folder the program runs in. */
    public static JudgeFiles workingDirectory() {
        Path root = Path.of("").toAbsolutePath();
        if (!Files.isRegularFile(root.resolve("cities.json"))) {
            throw new RouteFailureException(
                    "the judges run from the folder that holds cities.json, but " + root + " has none");
        }
        return new JudgeFiles(root);
    }

    public Path citiesFile() {
        return root.resolve("cities.json");
    }

    public Path settingsFile() {
        return root.resolve("settings.json");
    }

    public Path mapsFolder() {
        return root.resolve("data").resolve("maps");
    }

    public Path graphsFolder() {
        return root.resolve("data").resolve("graphs");
    }
}
