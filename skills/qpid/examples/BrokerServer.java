package com.example;

import org.apache.qpid.server.SystemLauncher;

import java.util.HashMap;
import java.util.Map;

public class BrokerServer {

    private final SystemLauncher systemLauncher = new SystemLauncher();

    public void start() throws Exception {
        Map<String, Object> attributes = new HashMap<>();
        attributes.put("type", "Memory");
        attributes.put("initialConfigurationLocation",
                getClass().getClassLoader().getResource("qpid-initial-config.json").toExternalForm());
        attributes.put("startupLoggedToSystemOut", true);
        systemLauncher.startup(attributes);
    }

    public void stop() {
        systemLauncher.shutdown();
    }

    public static void main(String[] args) throws Exception {
        BrokerServer server = new BrokerServer();
        server.start();
        Runtime.getRuntime().addShutdownHook(new Thread(server::stop));
    }
}
