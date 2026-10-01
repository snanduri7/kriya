package com.example;

import org.apache.ignite.Ignite;
import org.apache.ignite.IgniteCache;
import org.apache.ignite.Ignition;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.context.support.ClassPathXmlApplicationContext;

public class IgniteDemoApp {
    public static void main(String[] args) {
        // Use Spring ApplicationContext to start and manage the Ignite node
        try (ConfigurableApplicationContext context = new ClassPathXmlApplicationContext("ignite-config.xml")) {
            // Retrieve the initialized Ignite bean from the context by its bean ID
            Ignite ignite = (Ignite) context.getBean("igniteNode");

            // Create or get a cache named "my-cache"
            IgniteCache<String, String> cache = ignite.getOrCreateCache("my-cache");

            // Put a value into the cache
            cache.put("key1", "Hello, Ignite!");

            // Get the value back from the cache
            String value = cache.get("key1");

            // Print the retrieved value
            System.out.println("Retrieved value from cache: " + value);

            // Verification result
            if ("Hello, Ignite!".equals(value)) {
                System.out.println("[VERIFICATION] PASS");
            } else {
                System.out.println("[VERIFICATION] FAIL: Retrieved value does not match expected");
            }
        }
        // The context is automatically closed here due to try-with-resources,
        // which shuts down the Ignite node managed by IgniteSpringBean
    }
}
