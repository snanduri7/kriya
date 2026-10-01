"""D2B model-free reproducer: does the candidate application execute during
Kriya's Maven runtime acquisition (with registry network authority)?

Real OCI containment (Docker), a fresh per-workspace Maven cache (so the exec
plugin is genuinely missing), no model. The candidate's main() appends one
line per execution to ran.log: the JVM proxy it can see and what an HTTPS
request to the Maven registry returns from inside the candidate.
1. greenfield Maven app; 2. Kriya's compile gate (contained, its own bounded
acquisition for compile); 3. Kriya's runtime step for the model's command
`mvn -q exec:java` (validator._run_runtime_step, the D2 path, called directly
so the D3 prerequisite is not involved); 4. print ran.log.
"""
import json
import os
import shutil
import tempfile

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator

POM = """<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
<groupId>demo</groupId><artifactId>d2b</artifactId><version>1</version>
<properties><maven.compiler.release>17</maven.compiler.release>
<project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties></project>
"""
APP = r'''package demo;
import java.io.FileWriter;
import java.net.HttpURLConnection;
import java.net.URL;
public class App {
  public static void main(String[] a) throws Exception {
    String http;
    try {
      HttpURLConnection c = (HttpURLConnection) new URL("https://repo.maven.apache.org/maven2/").openConnection();
      c.setConnectTimeout(5000); c.setReadTimeout(5000);
      http = "HTTP " + c.getResponseCode();
    } catch (Exception e) { http = "no network (" + e.getClass().getSimpleName() + ")"; }
    try (FileWriter w = new FileWriter("ran.log", true)) {
      w.write("CANDIDATE_MAIN_RAN https.proxyHost=" + System.getProperty("https.proxyHost") + " registry=" + http + "\n");
    }
  }
}
'''
SOURCE = "src/main/java/demo/App.java"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="kriya-d2b-") as tmp:
        ws = os.path.realpath(tmp)
        os.makedirs(os.path.join(ws, "src/main/java/demo"))
        open(os.path.join(ws, "pom.xml"), "w").write(POM)
        open(os.path.join(ws, SOURCE), "w").write(APP)
        validator = PolymorphicValidator(ws, autonomy_cfg=AutonomyConfig(
            contained_execution_required=True, containment_backend="oci"))
        compiled = validator.run_compile_check([SOURCE])
        log = os.path.join(ws, "ran.log")
        if os.path.exists(log):
            os.remove(log)
        exec_cached = os.path.isdir(os.path.join(ws, ".kriya/m2_cache/org/codehaus/mojo/exec-maven-plugin"))
        result = validator._run_runtime_step(["mvn", "-q", "exec:java", "-Dexec.mainClass=demo.App"], 120)  # pylint: disable=protected-access
        runs = open(log).read().splitlines() if os.path.exists(log) else []
        print(json.dumps({"compile_success": compiled["success"], "exec_plugin_cached_before_runtime": exec_cached,
                          "runtime_returncode": result["returncode"], "candidate_executions": len(runs),
                          "executions": runs}, indent=1))
        shutil.rmtree(os.path.join(ws, ".kriya"), ignore_errors=True)


if __name__ == "__main__":
    main()
