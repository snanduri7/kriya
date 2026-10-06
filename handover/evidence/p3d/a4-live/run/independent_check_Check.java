import org.apache.commons.cli.*;
public class Check {
    static void eq(boolean got, boolean exp, String n){ if(got!=exp) throw new AssertionError(n+": got "+got); System.out.println("PASS "+n+" -> "+got); }
    public static void main(String[] x) throws Exception {
        Options o = new Options(); o.addOption(Option.builder("a").longOpt("alpha").build()); o.addOption("b", false, "b");
        CommandLine cl = new DefaultParser().parse(o, new String[]{"-a"});
        eq(cl.hasAnyOption("b","a"), true, "hasAnyOption(\"b\",\"a\")");
        eq(cl.hasAnyOption("a","x"), true, "hasAnyOption(\"a\",\"x\")");
        eq(cl.hasAnyOption("alpha"), true, "hasAnyOption(\"alpha\")");
        eq(cl.hasAnyOption("b","x"), false, "hasAnyOption(\"b\",\"x\")");
        eq(cl.hasAnyOption(), false, "hasAnyOption()");
        eq(cl.hasAnyOption("alpha"), cl.hasOption("alpha"), "agrees with hasOption(alpha)");
        eq(cl.hasAnyOption("b"), cl.hasOption("b"), "agrees with hasOption(b)");
    }
}
