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
        byte[] encoded = parser.encode(original);
        Protocol decoded = parser.decode(encoded);

        try (ConfigurableApplicationContext context = new ClassPathXmlApplicationContext("ignite-config.xml")) {
            Ignite ignite = (Ignite) context.getBean("igniteNode");
            IgniteCache<Integer, Protocol> cache = ignite.getOrCreateCache("protocol-cache");
            cache.put(1, decoded);
            Protocol fromCache = cache.get(1);

            boolean equal = original.equals(fromCache);
            System.out.println("[RESULT] protocolVersion=" + fromCache.getProtocolVersion()
                    + ", softwareVersion=" + fromCache.getSoftwareVersion()
                    + ", dataLength=" + fromCache.getDataLength()
                    + ", time=" + fromCache.getTime()
                    + ", bodyLength=" + fromCache.getBody().length
                    + ", equals=" + equal);
            if (!equal) {
                throw new IllegalStateException("Protocol read back from the Ignite cache differs from the original");
            }
            System.out.println("[VERIFICATION] PASS");
        }
    }
}
