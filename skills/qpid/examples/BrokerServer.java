import org.apache.qpid.server.SystemLauncher;
import java.util.HashMap;
import java.util.Map;

public class BrokerServer {
    public static void main(String[] args) throws Exception {
        SystemLauncher systemLauncher = new SystemLauncher();
        Map<String, Object> configMap = new HashMap<>();
        configMap.put("type", "Memory");
        configMap.put("initialConfigurationLocation", BrokerServer.class.getClassLoader().getResource("qpid-initial-config.json").toExternalForm());
        configMap.put("initialSystemPropertiesLocation", BrokerServer.class.getClassLoader().getResource("system.properties").toExternalForm());
        configMap.put("startupLoggedToSystemOut", true);
        systemLauncher.startup(configMap);
        // ... run application logic here ...
        systemLauncher.shutdown();
    }
}