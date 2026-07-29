package com.example;

import org.apache.activemq.artemis.core.config.Configuration;
import org.apache.activemq.artemis.core.config.impl.ConfigurationImpl;
import org.apache.activemq.artemis.core.server.ActiveMQServer;
import org.apache.activemq.artemis.core.server.impl.ActiveMQServerImpl;

public class BrokerServer {
    public static void main(String[] args) throws Exception {
        Configuration config = new ConfigurationImpl();
        config.addAcceptorConfiguration("amqp", "tcp://localhost:5672?protocols=AMQP");
        config.setSecurityEnabled(false);
        config.setPersistenceEnabled(false);
        config.setJournalDirectory("target/journal");
        config.setBindingsDirectory("target/bindings");
        config.setLargeMessagesDirectory("target/largemessages");
        config.setPagingDirectory("target/paging");

        ActiveMQServer server = new ActiveMQServerImpl(config);
        server.start();
    }
}
