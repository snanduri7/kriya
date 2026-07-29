# Embedded ActiveMQ Artemis Broker Server Skill

This skill provides the instructions and rules to set up an embedded ActiveMQ server in Java with AMQP 1.0 protocol support.

## Dependency Setup
Add the following dependencies to the `pom.xml`:
```xml
<dependency>
    <groupId>org.apache.activemq</groupId>
    <artifactId>artemis-server</artifactId>
    <version>2.31.2</version>
</dependency>
<dependency>
    <groupId>org.apache.activemq</groupId>
    <artifactId>artemis-amqp-protocol</artifactId>
    <version>2.31.2</version>
</dependency>
```

## Java Setup
To run the server programmatically:
```java
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
```
