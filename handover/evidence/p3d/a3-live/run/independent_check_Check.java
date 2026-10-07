public class Check {
    public static int clamp(final int value, final int min, final int max) {
        if (min > max) {
            throw new IllegalArgumentException("min cannot be greater than max.");
        }
        if (value < min) {
            return min;
        }
        if (value > max) {
            return max;
        }
        return value;
    }
    static void eq(int got, int exp, String n){ if(got!=exp) throw new AssertionError(n+": got "+got+" expected "+exp); System.out.println("PASS "+n); }
    public static void main(String[] a){
        eq(clamp(-5,0,10),0,"below min -> min"); eq(clamp(Integer.MIN_VALUE,-3,3),-3,"MIN_VALUE -> min");
        eq(clamp(15,0,10),10,"above max -> max"); eq(clamp(Integer.MAX_VALUE,-3,3),3,"MAX_VALUE -> max");
        eq(clamp(5,0,10),5,"inside"); eq(clamp(0,0,10),0,"== min"); eq(clamp(10,0,10),10,"== max"); eq(clamp(7,7,7),7,"min==max");
        try { clamp(5,10,0); throw new AssertionError("no IAE"); } catch (IllegalArgumentException e) { System.out.println("PASS min>max -> IAE"); }
        try { clamp(10,10,9); throw new AssertionError("no IAE"); } catch (IllegalArgumentException e) { System.out.println("PASS min>max (value==min) -> IAE"); }
    }
}
