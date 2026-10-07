import org.apache.commons.lang3.CharSetUtils;
public class Check {
    public static void main(String[] a) {
        System.out.println("containsOnly(\"hello\", (String) null) = " + CharSetUtils.containsOnly("hello", (String) null) + "   (goal: false)");
        System.out.println("containsOnly(\"hello\", \"\")           = " + CharSetUtils.containsOnly("hello", "") + "   (goal: false)");
        System.out.println("containsOnly(\"hello\", \"a-z\")        = " + CharSetUtils.containsOnly("hello", "a-z") + "   (goal: true)");
        System.out.println("containsOnly(\"hello\", \"a-d\")        = " + CharSetUtils.containsOnly("hello", "a-d") + "   (goal: false)");
        System.out.println("containsOnly(null, \"a\")             = " + CharSetUtils.containsOnly(null, "a") + "   (goal: true)");
    }
}
