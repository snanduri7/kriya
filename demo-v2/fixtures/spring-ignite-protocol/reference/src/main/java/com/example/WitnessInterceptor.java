package com.example;

import com.example.protocol.Protocol;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import javax.cache.Cache;
import org.apache.ignite.cache.CacheInterceptorAdapter;

/**
 * Cache-side witness. With offset 0 (the default, and always when no file ./cache-witness exists) it changes nothing.
 * With a non-zero offset the cache itself stores the protocol with {@code time} moved by that offset, so a program can only
 * report the shifted time if it really used the value the cache returned.
 */
public class WitnessInterceptor extends CacheInterceptorAdapter<Integer, Protocol> {
    private long timeOffset;

    public WitnessInterceptor() {
        Path file = Path.of("cache-witness");
        if (Files.isRegularFile(file)) {
            try {
                timeOffset = Long.parseLong(Files.readString(file).trim());
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
    }

    public void setTimeOffset(long timeOffset) {
        this.timeOffset = timeOffset;
    }

    @Override
    public Protocol onBeforePut(Cache.Entry<Integer, Protocol> entry, Protocol newVal) {
        if (timeOffset == 0) {
            return newVal;
        }
        return new Protocol(newVal.getProtocolVersion(), newVal.getSoftwareVersion(), newVal.getTime() + timeOffset, newVal.getBody());
    }
}
