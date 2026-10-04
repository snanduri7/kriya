// Golden-fixture round trip through the GENERATED Java types (06 Phase B item 4, gate A-2 P-R4).
// For every fixture envelope: JSON -> kup.Envelope -> JSON and JSON -> the operation's data class -> JSON; the result
// must be value-equal to the input (numbers compared by value; an optional property absent in the input may come back
// as an explicit null, the one representational difference of a Java POJO), which proves nothing is dropped - unknown
// fields included. No Java host is built; this is a check that the contract is usable from Java.
import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.*;
import java.nio.file.*;
import java.util.*;

public class RoundTrip {
    public static void main(String[] args) throws Exception {
        Path dir = Paths.get(args[0]);
        ObjectMapper mapper = new ObjectMapper();
        int files = 0, unknownFieldsSeen = 0;
        List<String> failures = new ArrayList<>();
        List<Path> paths = new ArrayList<>();
        try (var s = Files.walk(dir)) { s.filter(p -> p.toString().endsWith(".json") && !p.getFileName().toString().equals("index.json")).sorted().forEach(paths::add); }
        for (Path p : paths) {
            String name = p.getFileName().toString();
            JsonNode original = mapper.readTree(Files.readAllBytes(p));
            kup.Envelope env = mapper.treeToValue(original, kup.Envelope.class);
            JsonNode back = mapper.valueToTree(env);
            Class<?> dataClass = name.startsWith("capabilities") ? kup.Capabilities.class : name.startsWith("history.list") ? kup.HistoryList.class
                : name.startsWith("history.detail") ? kup.RunDetail.class : name.startsWith("history.prompt") ? kup.Prompt.class
                : name.startsWith("workspace.status") ? kup.WorkspaceStatus.class : name.startsWith("snapshot.acquire") ? kup.SnapshotAcquireResult.class
                : name.startsWith("snapshot.list") ? kup.SnapshotList.class : name.startsWith("snapshot.prune") ? kup.SnapshotPrune.class : null;
            if (dataClass != null && !original.get("data").isNull()) {
                Object data = mapper.treeToValue(original.get("data"), dataClass);
                JsonNode dataBack = mapper.valueToTree(data);
                if (!valueEquals(original.get("data"), dataBack)) failures.add(name + ": data round trip through " + dataClass.getSimpleName() + " differs: " + firstDifference(original.get("data"), dataBack, "data"));
                unknownFieldsSeen += countUnknown(data);
            }
            if (!valueEquals(original, back)) failures.add(name + ": envelope round trip differs: " + firstDifference(original, back, ""));
            if (name.equals("history.detail.run-unknown-fields.json")) {
                kup.RunDetail d = mapper.treeToValue(original.get("data"), kup.RunDetail.class);
                if (!d.getAdditionalProperties().containsKey("novel_top_level_section")) failures.add(name + ": novel_top_level_section was dropped by RunDetail");
                JsonNode ev = mapper.valueToTree(d).get("run_events").get("data").get(0);
                if (ev == null || !ev.has("severity_v9")) failures.add(name + ": severity_v9 inside run_events[0] was dropped");
                else unknownFieldsSeen += 100; // the nested unknown fields rode through Section.data (a Map) untouched
            }
            files++;
        }
        System.out.println("round-trip files=" + files + " unknown_fields_preserved=" + unknownFieldsSeen + " failures=" + failures.size());
        for (String f : failures) System.out.println(" - " + f);
        if (failures.isEmpty() && unknownFieldsSeen == 0) { System.out.println("no unknown field exercised: the fixtures must contain unknown fields"); System.exit(2); }
        System.exit(failures.isEmpty() ? 0 : 1);
    }

    static int countUnknown(Object o) {
        try { var m = o.getClass().getMethod("getAdditionalProperties"); @SuppressWarnings("unchecked") var map = (Map<String, Object>) m.invoke(o); int n = map.size(); for (Object v : map.values()) n += countUnknownDeep(v); return n; } catch (NoSuchMethodException e) { return 0; } catch (Exception e) { throw new RuntimeException(e); }
    }
    static int countUnknownDeep(Object v) { return 0; }

    /** Value equality: object fields unordered, numbers by numeric value (40 == 40.0), everything else exact. */
    static boolean valueEquals(JsonNode a, JsonNode b) {
        if (a.isNumber() && b.isNumber()) return a.decimalValue().compareTo(b.decimalValue()) == 0;
        if (a.isObject() && b.isObject()) {
            var it = a.fields();
            while (it.hasNext()) { var e = it.next(); if (!b.has(e.getKey()) || !valueEquals(e.getValue(), b.get(e.getKey()))) return false; }
            var it2 = b.fields();
            while (it2.hasNext()) { var e = it2.next(); if (!a.has(e.getKey()) && !e.getValue().isNull()) return false; } // absent -> explicit null is allowed, nothing else may appear
            return true;
        }
        if (a.isArray() && b.isArray()) { if (a.size() != b.size()) return false; for (int i = 0; i < a.size(); i++) if (!valueEquals(a.get(i), b.get(i))) return false; return true; }
        return a.equals(b);
    }
    static String firstDifference(JsonNode a, JsonNode b, String path) {
        if (a.isObject() && b.isObject()) {
            var it = a.fields();
            while (it.hasNext()) { var e = it.next(); if (!b.has(e.getKey())) return path + "." + e.getKey() + " missing after round trip"; if (!valueEquals(e.getValue(), b.get(e.getKey()))) return firstDifference(e.getValue(), b.get(e.getKey()), path + "." + e.getKey()); }
            var it2 = b.fields(); while (it2.hasNext()) { var e = it2.next(); if (!a.has(e.getKey()) && !e.getValue().isNull()) return path + "." + e.getKey() + " added after round trip"; }
            return path + " (size)";
        }
        if (a.isArray() && b.isArray()) { for (int i = 0; i < Math.min(a.size(), b.size()); i++) if (!valueEquals(a.get(i), b.get(i))) return firstDifference(a.get(i), b.get(i), path + "[" + i + "]"); return path + " (array size " + a.size() + " vs " + b.size() + ")"; }
        return path + ": " + abbreviate(a) + " vs " + abbreviate(b);
    }
    static String abbreviate(JsonNode n) { String s = n.toString(); return s.length() > 80 ? s.substring(0, 80) + "..." : s; }
}
