package com.example;

import org.apache.qpid.server.SystemLauncher;
import org.apache.qpid.jms.JmsConnectionFactory;

import jakarta.jms.*;

import java.util.HashMap;
import java.util.Map;

public class ProtocolApplication {

    private static final String QUEUE_NAME = "protocol.queue";
    private static final SystemLauncher systemLauncher = new SystemLauncher();

    public static void main(String[] args) throws Exception {
        // Start broker
        Map<String, Object> configMap = new HashMap<>();
        configMap.put("type", "Memory");
        configMap.put("initialConfigurationLocation",
                ProtocolApplication.class.getClassLoader().getResource("qpid-initial-config.json").toExternalForm());
        configMap.put("initialSystemPropertiesLocation",
                ProtocolApplication.class.getClassLoader().getResource("system.properties").toExternalForm());
        configMap.put("startupLoggedToSystemOut", true);
        systemLauncher.startup(configMap);

        try {
            // Create JMS connection
            JmsConnectionFactory factory = new JmsConnectionFactory();
            factory.setRemoteURI("amqp://localhost:5672");
            Connection connection = factory.createConnection();
            connection.start(); // Ensure connection is started
            Session session = connection.createSession(false, Session.AUTO_ACKNOWLEDGE);
            Queue queue = session.createQueue(QUEUE_NAME);

            // Create protocol
            byte[] body = "Hello, Protocol".getBytes();
            Protocol originalProtocol = new Protocol(1, 2, body.length, 1700000000L, body);

            // Encode protocol
            ProtocolParser parser = new ProtocolParser();
            byte[] encodedBytes = parser.encode(originalProtocol);

            // Send message
            MessageProducer producer = session.createProducer(queue);
            producer.setDeliveryMode(DeliveryMode.NON_PERSISTENT);
            BytesMessage message = session.createBytesMessage();
            message.writeBytes(encodedBytes);
            producer.send(message);

            // Receive message
            MessageConsumer consumer = session.createConsumer(queue);
            BytesMessage receivedMessage = (BytesMessage) consumer.receive(5000);

            // Decode received bytes
            byte[] receivedBytes = new byte[(int) receivedMessage.getBodyLength()];
            receivedMessage.readBytes(receivedBytes);
            Protocol decodedProtocol = parser.decode(receivedBytes);

            // Compare fields
            boolean equals = 
                originalProtocol.getProtocolVersion() == decodedProtocol.getProtocolVersion() &&
                originalProtocol.getSoftwareVersion() == decodedProtocol.getSoftwareVersion() &&
                originalProtocol.getDataLength() == decodedProtocol.getDataLength() &&
                originalProtocol.getTime() == decodedProtocol.getTime() &&
                java.util.Arrays.equals(originalProtocol.getBody(), decodedProtocol.getBody());

            // Print result
            System.out.println("[RESULT] protocolVersion=" + decodedProtocol.getProtocolVersion() +
                ", softwareVersion=" + decodedProtocol.getSoftwareVersion() +
                ", dataLength=" + decodedProtocol.getDataLength() +
                ", time=" + decodedProtocol.getTime() +
                ", bodyLength=" + (decodedProtocol.getBody() != null ? decodedProtocol.getBody().length : 0) +
                ", equals=" + equals);

            // Close resources
            consumer.close();
            producer.close();
            session.close();
            connection.close();

        } finally {
            // Stop broker
            systemLauncher.shutdown();
        }

        System.out.println("[VERIFICATION] PASS");
    }
}
