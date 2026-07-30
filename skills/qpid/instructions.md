# Apache Qpid (incl. "Red Hat Qpid MRG") Embedded Broker + JMS Client Skill

"Red Hat Qpid MRG" (Messaging, Realtime, and Grid) was Red Hat's historical productized bundle of Apache Qpid; it is not a different broker. Requests naming "Qpid MRG" or "Red Hat AMQ" should be implemented with genuine Apache Qpid Broker-J (the embeddable Java broker) and the `qpid-jms` client - not ActiveMQ Artemis, which is a separate broker that happens to also support AMQP.

## Dependency Setup
Add the following to `pom.xml`. All `qpid-broker-*` artifacts must share the same version (they release in lockstep):
```xml
<!-- Embedded broker -->
<dependency>
    <groupId>org.apache.qpid</groupId>
    <artifactId>qpid-broker-core</artifactId>
    <version>9.2.1</version>
</dependency>
<dependency>
    <groupId>org.apache.qpid</groupId>
    <artifactId>qpid-broker-plugins-amqp-1-0-protocol</artifactId>
    <version>9.2.1</version>
</dependency>
<dependency>
    <groupId>org.apache.qpid</groupId>
    <artifactId>qpid-broker-plugins-memory-store</artifactId>
    <version>9.2.1</version>
</dependency>

<!-- JMS client (javax.jms variant - use 2.10.0 + jakarta.jms if the project targets Jakarta EE 9+/Spring 6+) -->
<dependency>
    <groupId>org.apache.qpid</groupId>
    <artifactId>qpid-jms-client</artifactId>
    <version>1.16.0</version>
</dependency>
```

## Embedding the Broker
```java
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
        new BrokerServer().start();
    }
}
```

`src/main/resources/qpid-initial-config.json` must declare the broker name, an AMQP 1.0 port with virtual host aliases, and at least one virtual host node with a queue auto-creation policy, e.g.:
```json
{
  "name": "EmbeddedBroker",
  "modelVersion": "8.0",
  "authenticationproviders": [
    { "name": "anonymous", "type": "Anonymous" }
  ],
  "ports": [
    {
      "name": "AMQP",
      "port": "5672",
      "authenticationProvider": "anonymous",
      "protocols": ["AMQP_1_0"],
      "virtualhostaliases": [
        { "name": "nameAlias", "type": "nameAlias" },
        { "name": "defaultAlias", "type": "defaultAlias" },
        { "name": "hostnameAlias", "type": "hostnameAlias" }
      ]
    }
  ],
  "virtualhostnodes": [
    {
      "name": "default",
      "type": "Memory",
      "defaultVirtualHostNode": true,
      "virtualHostInitialConfiguration": "{\"type\": \"Memory\", \"nodeAutoCreationPolicies\": [{\"pattern\":\".*\",\"createdOnPublish\":\"true\",\"createdOnConsume\":\"true\",\"nodeType\":\"queue\"}]}"
    }
  ]
}
```
Every field above is load-bearing, verified against real end-to-end runs (not just "compiles") - omitting any of them produces a broker that starts without error but silently can't actually carry a message:
- `"modelVersion": "8.0"` - this is the broker's internal domain-model schema version, NOT the qpid-broker-core artifact/release version. Setting it to the broker-core version (e.g. "9.2" for broker-core 9.2.1) fails with `IllegalConfigurationException: No phase upgrader for version 9.2` from inside `SystemLauncher.startup()` - which does NOT throw, so this fails completely silently and the AMQP port never binds. "8.0" is what qpid-broker-core itself ships as its own default `initial-config.json` across its 8.x/9.x/10.x releases.
- `"virtualhostaliases"` with a `defaultAlias` entry - without this, the client's AMQP `Open` frame hostname (which defaults to whatever host is in the connection URI, e.g. "localhost") can never resolve to any virtualhost, no matter what the virtualhostnode is named. Fails with `JmsResourceNotFoundException: Unknown hostname in connection open: '<host>' [condition = amqp:not-found]`.
- `"nodeAutoCreationPolicies"` on the virtualhost - `session.createQueue(name)` in JMS is purely a client-side reference; it does not create anything on the broker. Without an auto-creation policy, sending to a queue that doesn't already exist fails with `InvalidDestinationException: Could not find destination for target '...'`.

## JMS Client (Spring XML wiring)
`JmsConnectionFactory` has a no-arg constructor and a `setRemoteURI(String)` setter, so it wires directly as a Spring bean:
```xml
<bean id="qpidConnectionFactory" class="org.apache.qpid.jms.JmsConnectionFactory">
    <property name="remoteURI" value="amqp://localhost:5672"/>
</bean>
```

Standard `javax.jms` code against that factory:
```java
import javax.jms.*;

ConnectionFactory factory = (ConnectionFactory) applicationContext.getBean("qpidConnectionFactory");
try (Connection connection = factory.createConnection()) {
    connection.start();
    Session session = connection.createSession(false, Session.AUTO_ACKNOWLEDGE);
    Queue queue = session.createQueue("example-queue");

    MessageProducer producer = session.createProducer(queue);
    // The example broker config above uses a Memory (non-durable) store, which
    // cannot accept the JMS default PERSISTENT delivery mode - fails with
    // "Non-durable message store cannot accept durable message." Set it
    // explicitly, matching the official qpid-jms Sender.java example.
    producer.setDeliveryMode(DeliveryMode.NON_PERSISTENT);
    producer.send(session.createTextMessage("hello"));

    MessageConsumer consumer = session.createConsumer(queue);
    TextMessage received = (TextMessage) consumer.receive(5000);
}
```

## Running Broker + Client In The Same App

The "Embedding the Broker" and "JMS Client" sections above are two independent
pieces. If the goal requires BOTH a broker and a client living in the same process
(as opposed to a client connecting to some already-running external broker), a
class that only wires the `qpidConnectionFactory` bean - without ever calling
`SystemLauncher.startup(...)` - will compile fine but fail at runtime with a
connection-refused error, because nothing is listening on the AMQP port.

Rules for this combined case:
1. Start the broker (`SystemLauncher.startup(...)`) FIRST, before building the
   Spring `ApplicationContext` or creating any JMS connection - the AMQP port isn't
   listening until `startup()` returns.
2. Keep a reference to the `SystemLauncher` for the lifetime of the app; call
   `shutdown()` on it only after JMS resources (and the Spring context, if used)
   are closed.
3. See `examples/CombinedBrokerClientApp.java` for the full end-to-end pattern:
   start broker -> build Spring context -> send -> receive -> log -> close JMS ->
   close context -> stop broker.
