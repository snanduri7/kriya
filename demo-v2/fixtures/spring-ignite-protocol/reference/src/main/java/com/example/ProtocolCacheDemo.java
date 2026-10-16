package com.example;

import com.example.protocol.Protocol;
import org.apache.ignite.IgniteCache;

/** The Ignite round trip, kept in one place so the application and its test exercise the same code. */
public final class ProtocolCacheDemo {
    public static final String CACHE_NAME = "protocol-cache";
    private static final int KEY = 1;

    private ProtocolCacheDemo() {
    }

    /** Puts the protocol into the cache and returns what the cache holds for the key, read back with get. */
    public static Protocol roundTrip(IgniteCache<Integer, Protocol> cache, Protocol protocol) {
        cache.put(KEY, protocol);
        Protocol retrieved = cache.get(KEY);
        if (retrieved == null) {
            throw new IllegalStateException("the Ignite cache returned nothing for key " + KEY);
        }
        return retrieved;
    }

    /** The success line is built from the RETRIEVED protocol; only the comparison involves the original. */
    public static String resultLine(Protocol original, Protocol retrieved) {
        return "[RESULT] protocolVersion=" + retrieved.getProtocolVersion()
                + ", softwareVersion=" + retrieved.getSoftwareVersion()
                + ", dataLength=" + retrieved.getDataLength()
                + ", time=" + retrieved.getTime()
                + ", bodyLength=" + retrieved.getBody().length
                + ", equals=" + original.equals(retrieved);
    }
}
