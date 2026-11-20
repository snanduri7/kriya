package com.example;

import org.apache.qpid.server.SystemLauncher;
import org.apache.qpid.jms.JmsConnectionFactory;

import jakarta.jms.*;

import java.util.HashMap;
import java.util.Map;

public class ProtocolApplication {

    private static final String QUEUE_NAME = "protocol.queue";
    private static final String BROKER_PORT = "5672";

    public static void main(String[] args) {
        SystemLauncher systemLauncher = new SystemLauncher();
        try {
            // Start embedded broker
            Map<String, Object> attributes = new HashMap<>();
            attributes.put("type", "Memory");
            attributes.put("initialConfigurationLocation",
                    ProtocolApplication.class.getClassLoader().getResource("qpid-initial-config.json").toExternalForm());
            attributes.put("initialSystemPropertiesLocation",
                    ProtocolApplication.class.getClassLoader().getResource("system.properties").toExternalForm());
            attributes.put("startupLoggedToSystemOut", true);
            systemLauncher.startup(attributes);

            // Create sample protocol
            byte[] body = "Hello, Protocol".getBytes();
            Protocol originalProtocol = new Protocol(1, 2, body.length, 1700000000L, body);

            // Encode protocol
            byte[] encodedBytes = ProtocolParser.encode(originalProtocol);

            // Create JMS connection
            JmsConnectionFactory factory = new JmsConnectionFactory();
            factory.setRemoteURI("amqp://localhost:" + BROKER_PORT);
            
            try (Connection connection = factory.createConnection();
                 Session session = connection.createSession(false, Session.AUTO_ACKNOWLEDGE)) {
                
                connection.start();
                
                // Send message
                Queue queue = session.createQueue(QUEUE_NAME);
                MessageProducer producer = session.createProducer(queue);
                producer.setDeliveryMode(DeliveryMode.NON_PERSISTENT);
                
                BytesMessage message = session.createBytesMessage();
                message.writeBytes(encodedBytes);
                producer.send(message);

                // Receive message
                MessageConsumer consumer = session.createConsumer(queue);
                BytesMessage receivedMessage = (BytesMessage) consumer.receive(5000); // 5 second timeout
                
                if (receivedMessage == null) {
                    System.out.println("[RESULT] protocolVersion=ERROR, softwareVersion=ERROR, dataLength=ERROR, time=ERROR, bodyLength=ERROR, equals=false");
                    System.out.println("[VERIFICATION] FAIL: Timeout waiting for message reception");
                    return;
                }

                // Extract received bytes
                byte[] receivedBytes = new byte[(int) receivedMessage.getBodyLength()];
                receivedMessage.readBytes(receivedBytes);

                // Decode received bytes
                Protocol decodedProtocol = ProtocolParser.decode(receivedBytes);

                // Compare fields
                boolean versionMatch = originalProtocol.getProtocolVersion() == decodedProtocol.getProtocolVersion();
                boolean softwareVersionMatch = originalProtocol.getSoftwareVersion() == decodedProtocol.getSoftwareVersion();
                boolean dataLengthMatch = originalProtocol.getDataLength() == decodedProtocol.getDataLength();
                boolean timeMatch = originalProtocol.getTime() == decodedProtocol.getTime();
                boolean bodyMatch = java.util.Arrays.equals(originalProtocol.getBody(), decodedProtocol.getBody());

                boolean allMatch = versionMatch && softwareVersionMatch && dataLengthMatch && timeMatch && bodyMatch;

                System.out.printf("[RESULT] protocolVersion=%d, softwareVersion=%d, dataLength=%d, time=%d, bodyLength=%d, equals=%b%n",
                        originalProtocol.getProtocolVersion(),
                        originalProtocol.getSoftwareVersion(),
                        originalProtocol.getDataLength(),
                        originalProtocol.getTime(),
                        originalProtocol.getBody().length,
                        allMatch);

                if (allMatch) {
                    System.out.println("[VERIFICATION] PASS");
                } else {
                    System.out.println("[VERIFICATION] FAIL: Field comparison mismatch");
                }
            }
        } catch (Exception e) {
            System.err.println("Error in ProtocolApplication: " + e.getMessage());
            e.printStackTrace();
            System.out.println("[VERIFICATION] FAIL: Exception occurred - " + e.getMessage());
        } finally {
            // Stop broker
            systemLauncher.shutdown();
        }
    }
}
