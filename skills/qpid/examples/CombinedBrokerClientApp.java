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
        configMap.put("startupLoggedToSystemOut", true);
        systemLauncher.startup(configMap);

        // Then create client
        JmsConnectionFactory factory = new JmsConnectionFactory();
        factory.setRemoteURI("amqp://localhost:5672");
        Connection connection = factory.createConnection();
        Session session = connection.createSession(false, Session.AUTO_ACKNOWLEDGE);
        Queue queue = session.createQueue("testQueue");
        MessageProducer producer = session.createProducer(queue);
        producer.setDeliveryMode(DeliveryMode.NON_PERSISTENT); // Required for Memory store
        TextMessage message = session.createTextMessage("Hello, Qpid!");
        producer.send(message);

        connection.close();
        systemLauncher.shutdown();
    }
}