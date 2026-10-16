package com.example;

import com.example.protocol.Protocol;
import com.example.protocol.ProtocolParser;
import java.nio.charset.StandardCharsets;
import org.apache.ignite.Ignite;
import org.apache.ignite.IgniteCache;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.context.support.ClassPathXmlApplicationContext;

public class Main {
    public static void main(String[] args) {
        ProtocolParser parser = new ProtocolParser();
        Protocol original = new Protocol(1, 2, 1700000000L, "Hello, Protocol".getBytes(StandardCharsets.UTF_8));
        Protocol decoded = parser.decode(parser.encode(original));

        try (ConfigurableApplicationContext context = new ClassPathXmlApplicationContext("ignite-config.xml")) {
            Ignite ignite = (Ignite) context.getBean("igniteNode");
            IgniteCache<Integer, Protocol> cache = ignite.getOrCreateCache(ProtocolCacheDemo.CACHE_NAME);
            Protocol retrieved = ProtocolCacheDemo.roundTrip(cache, decoded);
            System.out.println(ProtocolCacheDemo.resultLine(original, retrieved));
            System.out.println(original.equals(retrieved) ? "[VERIFICATION] PASS" : "[VERIFICATION] FAIL");
        }
    }
}
