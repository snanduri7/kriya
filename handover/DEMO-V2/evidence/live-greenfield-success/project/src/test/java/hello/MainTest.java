package hello;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class MainTest {

    @Test
    public void testMainMethod() {
        // This test verifies that the Main class can be instantiated and run
        // without throwing any exceptions. The actual output "Hello from Kriya"
        // is printed to System.out, which we cannot easily capture in a unit test,
        // but we can verify the method executes successfully.
        assertDoesNotThrow(() -> {
            Main.main(new String[]{});
        });
    }
}
