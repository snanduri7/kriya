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
Because of package encapsulation starting in JDK 16+, Apache Ignite requires opening internal packages to access system unsafe and reflections. Any execution command (e.g. exec-maven-plugin or runner scripts) must pass these JVM flags:
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
--add-opens=java.desktop/java.awt.font=ALL-UNNAMED

### 2a. CRITICAL - the ONLY correct way to wire these into exec-maven-plugin: use `exec:exec`, NOT `exec:java`
**`<jvmArguments>` is NOT a real configuration parameter of exec-maven-plugin 3.1.0's `java`
goal at all** - confirmed by extracting and reading the actual mojo descriptor
(`META-INF/maven/plugin.xml`) from the real jar. Maven silently ignores unknown
`<configuration>` elements (a warning, not a hard error), so a pom using
`<mainClass>+<jvmArguments>` can *look* fine and even run successfully for an app that
happens not to need any of those flags - but the flags themselves are NEVER actually
applied. This is a real, previously-undetected bug repeated across this skill's own
guidance for a full session: it only surfaced once tested against Apache Ignite
specifically, which genuinely needs `--add-opens=java.base/java.nio=ALL-UNNAMED` (among
others) and fails with `ExceptionInInitializerError:
java.nio.DirectByteBuffer.address field is unavailable ... module java.base does not
"opens java.nio" to unnamed module` when the flags silently never apply.

The deeper reason: `exec:java` runs your main() **inside Maven's own already-started JVM
process** - by the time your code runs, that JVM's module system is already locked in, so
there is no way for ANY exec-maven-plugin parameter to retroactively add `--add-opens`
flags to it. JVM startup flags can only be set when a JVM is *started*, which `exec:java`
never does.

The fix: use the **`exec:exec`** goal instead, which spawns a genuinely new `java` process
you have full command-line control over - `<executable>java</executable>` with an
`<arguments>` list containing every `--add-opens` flag as its own `<argument>`, followed
by `<argument>-classpath</argument><classpath/>` (the bare, self-closing `<classpath/>`
element - not wrapped in `<argument>` - is exec-maven-plugin's own recognized placeholder
for the resolved project classpath), followed by the main class name as the final
argument. Use `${exec.mainClass}` (a property, with a sensible default) for that final
argument rather than a hardcoded literal class name, so it stays overridable via
`-Dexec.mainClass=...` on the command line - see examples/pom.xml in this skill for a
complete, verified-working reference (confirmed live: with the correct project's
dependencies, this exact shape starts a real Ignite node, gets/puts/retrieves a cache
value, and prints the result correctly).

```xml
<plugin>
    <groupId>org.codehaus.mojo</groupId>
    <artifactId>exec-maven-plugin</artifactId>
    <version>3.1.0</version>
    <configuration>
        <executable>java</executable>
        <arguments>
            <argument>--add-opens=java.base/java.lang=ALL-UNNAMED</argument>
            <argument>--add-opens=java.base/java.util=ALL-UNNAMED</argument>
            <argument>--add-opens=java.base/java.io=ALL-UNNAMED</argument>
            <argument>-classpath</argument>
            <classpath/>
            <argument>${exec.mainClass}</argument>
        </arguments>
    </configuration>
</plugin>
```
(the real, complete flag list from section 2 above goes as individual `<argument>` elements, abbreviated here for readability; run via `mvn -q compile exec:exec -Dexec.mainClass=com.example.App`, not `exec:java`)

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
There are two valid ways to start Ignite with Spring XML - use EXACTLY ONE,
never both together: if your XML defines an `IgniteSpringBean` (Method B),
it auto-starts the node the instant the Spring context loads, so a
subsequent `Ignition.start(...)` call (Method A) on that same XML parses it
as a second, independent Spring context and starts a SECOND node under the
identical instance name - guaranteed "Ignite instance with this name has
already been started" at runtime, confirmed live, regardless of how correct
the rest of the code is.
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
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.context.support.ClassPathXmlApplicationContext;

public class App {
    public static void main(String[] args) {
        // Declare as ConfigurableApplicationContext (or ClassPathXmlApplicationContext
        // directly), NEVER the bare ApplicationContext interface - ApplicationContext has
        // no close() method and does not extend Closeable/AutoCloseable, so
        // `try (ApplicationContext context = ...)` fails to compile. Wrapping the whole
        // body in try-with-resources here is what actually stops the Ignite node when
        // main() finishes - closing the context triggers Spring's own bean-destruction
        // lifecycle, which stops the IgniteSpringBean-managed node as part of it. Without
        // this, the app can print every expected output and still hang forever afterward,
        // because nothing ever asked Ignite's background threads to stop - see the
        // resource-lifecycle rule in rules.txt.
        try (ConfigurableApplicationContext context = new ClassPathXmlApplicationContext("ignite-config.xml")) {
            // Retrieve the initialized Ignite bean from the context BY ITS BEAN
            // ID, not by type - context.getBean(Ignite.class) throws
            // NoSuchBeanDefinitionException, since the bean is only registered
            // under the id "igniteNode" given to it above, not the Ignite type.
            Ignite ignite = (Ignite) context.getBean("igniteNode");

            // Do NOT also call Ignition.start(...) here - see Method A above.
            // Write/read cache...
        }
        // context.close() already happened here (implicit via try-with-resources) -
        // do NOT also call ignite.close() separately; closing the context is the
        // correct and sufficient way to stop a Spring-managed IgniteSpringBean.
    }
}


