import org.apache.qpid.server.SystemLauncher;
import org.apache.qpid.jms.JmsConnectionFactory;
import javax.jms.*;
import java.util.HashMap;
import java.util.Map;

public class CombinedBrokerClientApp {
    public static void main(String[] args) throws Exception {
        // Start broker first
        SystemLauncher systemLauncher = new SystemLauncher();
        Map<String, Object> configMap = new HashMap<>();
        configMap.put("type", "Memory");
        configMap.put("initialConfigurationLocation", CombinedBrokerClientApp.class.getClassLoader().getResource("qpid-initial-config.json").toExternalForm());
        configMap.put("initialSystemPropertiesLocation", CombinedBrokerClientApp.class.getClassLoader().getResource("system.properties").toExternalForm());
        systemLauncher.startup(configMap);

        // Then create JMS connection
        JmsConnectionFactory cf = new JmsConnectionFactory();
        cf.setRemoteURI("amqp://localhost:5672");
        Connection conn = cf.createConnection();
        Session session = conn.createSession(false, Session.AUTO_ACKNOWLEDGE);
        // ... rest of JMS usage ...

        systemLauncher.shutdown();
    }
}