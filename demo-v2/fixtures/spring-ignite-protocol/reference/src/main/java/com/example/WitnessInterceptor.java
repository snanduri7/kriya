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
 * With a non-zero offset the cache itself stores a DIFFERENT protocol: {@code time} moved by that offset, protocolVersion and
 * softwareVersion raised by one, and one byte appended to the body (so dataLength changes too). A program can only report
 * those values if it really used the whole value the cache returned.
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
        byte[] body = newVal.getBody();
        byte[] longer = java.util.Arrays.copyOf(body, body.length + 1);
        longer[body.length] = '!';
        return new Protocol(newVal.getProtocolVersion() + 1, newVal.getSoftwareVersion() + 1, newVal.getTime() + timeOffset, longer);
    }
}
