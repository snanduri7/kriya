package com.example;

import org.apache.qpid.server.SystemLauncher;
import org.springframework.context.support.ClassPathXmlApplicationContext;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.jms.*;
import java.util.HashMap;
import java.util.Map;

/**
 * Reference pattern for a single process that embeds BOTH the Qpid broker and a
 * JMS client wired via Spring XML - not a client connecting to some already-running
 * external broker. The broker must be started and fully up BEFORE the Spring
 * context (and therefore the JmsConnectionFactory bean) is created, otherwise the
 * client's connectionFactory.createConnection() call fails with a connection-refused
 * error at runtime even though the code compiles cleanly.
 */
public class CombinedBrokerClientApp {

    private static final Logger logger = LoggerFactory.getLogger(CombinedBrokerClientApp.class);

    public static void main(String[] args) throws Exception {
        SystemLauncher broker = new SystemLauncher();
        Map<String, Object> brokerAttributes = new HashMap<>();
        brokerAttributes.put("type", "Memory");
        brokerAttributes.put("initialConfigurationLocation",
                CombinedBrokerClientApp.class.getClassLoader().getResource("qpid-initial-config.json").toExternalForm());
        brokerAttributes.put("startupLoggedToSystemOut", true);
        broker.startup(brokerAttributes);
        logger.info("Embedded Qpid broker started.");

        ClassPathXmlApplicationContext context = null;
        Connection connection = null;
        try {
            // Only safe to build the Spring context (and its qpidConnectionFactory bean)
            // AFTER broker.startup() above has returned - the AMQP port isn't listening
            // until then.
            context = new ClassPathXmlApplicationContext("applicationContext.xml");
            ConnectionFactory connectionFactory = (ConnectionFactory) context.getBean("qpidConnectionFactory");

            connection = connectionFactory.createConnection();
            connection.start();
            Session session = connection.createSession(false, Session.AUTO_ACKNOWLEDGE);
            Queue queue = session.createQueue("example-queue");

            MessageProducer producer = session.createProducer(queue);
            // The example broker config uses a Memory (non-durable) store, which cannot
            // accept the JMS default PERSISTENT delivery mode - see the official qpid-jms
            // Sender.java example, which does the same for the same reason.
            producer.setDeliveryMode(DeliveryMode.NON_PERSISTENT);
            producer.send(session.createTextMessage("hello"));

            MessageConsumer consumer = session.createConsumer(queue);
            TextMessage received = (TextMessage) consumer.receive(5000);
            if (received != null) {
                logger.info("Received message: {}", received.getText());
            } else {
                logger.warn("No message received within timeout.");
            }
        } finally {
            // Close JMS resources and the Spring context before shutting down the broker
            // they depend on.
            if (connection != null) {
                connection.close();
            }
            if (context != null) {
                context.close();
            }
            broker.shutdown();
            logger.info("Embedded Qpid broker stopped.");
        }
    }
}
