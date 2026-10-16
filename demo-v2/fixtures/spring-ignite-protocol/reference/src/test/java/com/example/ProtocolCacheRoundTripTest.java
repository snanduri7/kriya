package com.example;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotSame;

import com.example.protocol.Protocol;
import com.example.protocol.ProtocolParser;
import java.nio.charset.StandardCharsets;
import org.apache.ignite.Ignite;
import org.apache.ignite.IgniteCache;
import org.junit.jupiter.api.Test;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.context.support.ClassPathXmlApplicationContext;

/** Real embedded Ignite nodes started from Spring XML: no mock, no stub. */
class ProtocolCacheRoundTripTest {
    private static final Protocol ORIGINAL = new Protocol(1, 2, 1700000000L, "Hello, Protocol".getBytes(StandardCharsets.UTF_8));

    private static Protocol roundTrip(String springXml) {
        ProtocolParser parser = new ProtocolParser();
        Protocol decoded = parser.decode(parser.encode(ORIGINAL));
        try (ConfigurableApplicationContext context = new ClassPathXmlApplicationContext(springXml)) {
            Ignite ignite = (Ignite) context.getBean("igniteNode");
            IgniteCache<Integer, Protocol> cache = ignite.getOrCreateCache(ProtocolCacheDemo.CACHE_NAME);
            return ProtocolCacheDemo.roundTrip(cache, decoded);
        }
    }

    @Test
    void aProtocolSurvivesAnIgniteCacheRoundTripWithEveryFieldIntact() {
        Protocol retrieved = roundTrip("ignite-test-plain.xml");
        assertNotSame(ORIGINAL, retrieved);
        assertEquals(1, retrieved.getProtocolVersion());
        assertEquals(2, retrieved.getSoftwareVersion());
        assertEquals(15, retrieved.getDataLength());
        assertEquals(1700000000L, retrieved.getTime());
        assertArrayEquals("Hello, Protocol".getBytes(StandardCharsets.UTF_8), retrieved.getBody());
        assertEquals(ORIGINAL, retrieved);
        assertEquals("[RESULT] protocolVersion=1, softwareVersion=2, dataLength=15, time=1700000000, bodyLength=15, equals=true",
                ProtocolCacheDemo.resultLine(ORIGINAL, retrieved));
    }

    @Test
    void theReturnedProtocolIsWhatTheCacheHoldsNotWhatWasPut() {
        // This cache stores every protocol with its time moved by 5000, so only a value really read back from the cache carries it.
        Protocol retrieved = roundTrip("ignite-test-witness.xml");
        assertEquals(ORIGINAL.getTime() + 5000, retrieved.getTime());
        assertEquals(ORIGINAL.getProtocolVersion(), retrieved.getProtocolVersion());
        assertArrayEquals(ORIGINAL.getBody(), retrieved.getBody());
        assertNotEquals(ORIGINAL, retrieved);
        assertEquals("[RESULT] protocolVersion=1, softwareVersion=2, dataLength=15, time=1700005000, bodyLength=15, equals=false",
                ProtocolCacheDemo.resultLine(ORIGINAL, retrieved));
    }
}
