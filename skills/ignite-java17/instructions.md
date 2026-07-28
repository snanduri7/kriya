# instructions for ignite-java17

# Running Apache Ignite 2.18.0 on Java 17

When generating a Spring XML-based Apache Ignite application using Java 17 and Maven, adhere to the following setup instructions:

## 1. Required Maven Dependencies
Add these dependencies to the `pom.xml`:
```xml
<dependency>
    <groupId>org.apache.ignite</groupId>
    <artifactId>ignite-core</artifactId>
    <version>2.18.0</version>
</dependency>
<dependency>
    <groupId>org.apache.ignite</groupId>
    <artifactId>ignite-spring</artifactId>
    <version>2.18.0</version>
</dependency>

##2. Java 17 JVM Command Line Arguments
Because of package encapsulation starting in JDK 16+, Apache Ignite requires opening internal packages to access system unsafe and reflections. Ensure any execution command (e.g. exec-maven-plugin or runner scripts) passes the following JVM parameters:
--add-opens=java.base/jdk.internal.access=ALL-UNNAMED
--add-opens=java.base/jdk.internal.misc=ALL-UNNAMED
--add-opens=java.base/sun.nio.ch=ALL-UNNAMED
--add-opens=java.base/sun.util.calendar=ALL-UNNAMED
--add-opens=java.management/com.sun.jmx.mbeanserver=ALL-UNNAMED
--add-opens=jdk.internal.jvmstat/sun.jvmstat.monitor=ALL-UNNAMED
--add-opens=java.base/sun.reflect.generics.reflectiveObjects=ALL-UNNAMED
--add-opens=jdk.management/com.sun.management.internal=ALL-UNNAMED
--add-opens=java.base/java.io=ALL-UNNAMED
--add-opens=java.base/java.nio=ALL-UNNAMED
--add-opens=java.base/java.net=ALL-UNNAMED
--add-opens=java.base/java.util=ALL-UNNAMED
--add-opens=java.base/java.util.concurrent=ALL-UNNAMED
--add-opens=java.base/java.util.concurrent.locks=ALL-UNNAMED
--add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED
--add-opens=java.base/java.lang=ALL-UNNAMED
--add-opens=java.base/java.lang.invoke=ALL-UNNAMED
--add-opens=java.base/java.math=ALL-UNNAMED
--add-opens=java.sql/java.sql=ALL-UNNAMED
--add-opens=java.base/java.lang.reflect=ALL-UNNAMED
--add-opens=java.base/java.time=ALL-UNNAMED
--add-opens=java.base/java.text=ALL-UNNAMED
--add-opens=java.management/sun.management=ALL-UNNAMED
--add-opens java.desktop/java.awt.font=ALL-UNNAMED

##3. Spring XML Bean Configuration Example
When configuring the Ignite instance in Spring XML, use:
<bean id="ignite.cfg" class="org.apache.ignite.configuration.IgniteConfiguration">
    <property name="igniteInstanceName" value="ignite-server-node"/>
    <property name="peerClassLoadingEnabled" value="true"/>
</bean>

## 4. API Usage Rules
- `Ignition.start(...)` returns an `Ignite` object.
- To create a cache, use the `Ignite` instance:
  ```java
  Ignite ignite = Ignition.start("ignite-config.xml");
  IgniteCache<Integer, String> cache = ignite.getOrCreateCache("my-cache");

## 5. Starting Ignite with Spring XML Configuration
There are two valid ways to start Ignite with Spring XML:
### Method A: Direct Ignition (Recommended)
You do not need to initialize a Spring `ApplicationContext` in Java. Ignite's `Ignition.start(...)` automatically loads and parses the XML file:
```java
import org.apache.ignite.Ignite;
import org.apache.ignite.Ignition;
public class App {
    public static void main(String[] args) {
        // Just pass the filename directly
        try (Ignite ignite = Ignition.start("ignite-config.xml")) {
            // Write/read cache...
        }
    }
}

### Method B:Spring ApplicationContext Container
If you prefer starting it via a Spring ClassPathXmlApplicationContext, you must define the IgniteSpringBean in your XML and retrieve it from the context:

ignite-config.xml:
<bean id="ignite.cfg" class="org.apache.ignite.configuration.IgniteConfiguration">
    <property name="igniteInstanceName" value="ignite-server-node"/>
</bean>

<!-- This bean initializes the Ignite node container automatically -->
<bean id="igniteNode" class="org.apache.ignite.IgniteSpringBean">
    <property name="configuration" ref="ignite.cfg"/>
</bean>

App.java:
import org.apache.ignite.Ignite;
import org.springframework.context.ApplicationContext;
import org.springframework.context.support.ClassPathXmlApplicationContext;

public class App {
    public static void main(String[] args) {
        ApplicationContext context = new ClassPathXmlApplicationContext("ignite-config.xml");
        
        // Retrieve the initialized Ignite bean from the context
        Ignite ignite = context.getBean(Ignite.class);
        
        // Write/read cache...
    }
}


