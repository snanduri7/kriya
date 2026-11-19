package com.example;

import org.apache.qpid.server.SystemLauncher;
import org.apache.qpid.jms.JmsConnectionFactory;

import jakarta.jms.*;

import java.util.HashMap;
import java.util.Map;

public class ProtocolApplication {
    private static final String QUEUE_NAME = "protocol.queue";
    
    public static void main(String[] args) throws Exception {
        // Start embedded broker
        SystemLauncher systemLauncher = new SystemLauncher();
        Map<String, Object> configMap = new HashMap<>();
        configMap.put("type", "Memory");
        configMap.put("initialConfigurationLocation", 
                ProtocolApplication.class.getClassLoader().getResource("qpid-initial-config.json").toExternalForm());
        configMap.put("initialSystemPropertiesLocation", 
                ProtocolApplication.class.getClassLoader().getResource("system.properties").toExternalForm());
        configMap.put("startupLoggedToSystemOut", true);
        systemLauncher.startup(configMap);

        try {
            // Create sample protocol
            byte[] body = "Hello, Protocol".getBytes("UTF-8");
            Protocol originalProtocol = new Protocol(1, 2, body.length, 1700000000L, body);
            
            // Encode protocol
            ProtocolParser parser = new ProtocolParser();
            byte[] encodedBytes = parser.encode(originalProtocol);
            
            // Send via JMS
            JmsConnectionFactory factory = new JmsConnectionFactory();
            factory.setRemoteURI("amqp://localhost:5672");
            try (Connection connection = factory.createConnection()) {
                connection.start();
                Session session = connection.createSession(false, Session.AUTO_ACKNOWLEDGE);
                Queue queue = session.createQueue(QUEUE_NAME);
                MessageProducer producer = session.createProducer(queue);
                producer.setDeliveryMode(DeliveryMode.NON_PERSISTENT);
                
                BytesMessage message = session.createBytesMessage();
                message.writeBytes(encodedBytes);
                producer.send(message);

                // Receive and decode
                MessageConsumer consumer = session.createConsumer(queue);
                BytesMessage receivedMessage = (BytesMessage) consumer.receive(5000);
                
                if (receivedMessage == null) {
                    throw new RuntimeException("Timeout waiting for message");
                }
                
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
                
                if (!equals) {
                    System.err.println("Protocol comparison failed!");
                    System.exit(1);
                }
            }
        } finally {
            // Stop broker
            systemLauncher.shutdown();
        }
        
        System.out.println("[VERIFICATION] PASS");
    }
}
