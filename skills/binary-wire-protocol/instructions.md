# instructions for binary-wire-protocol

# Encoding/decoding a fixed-width binary wire protocol in Java

When a goal specifies an exact byte-level wire format (a header of named fields, each
with its own declared byte width, e.g. "protocolVersion (1 byte), dataLength (3 bytes,
big-endian), time (4 bytes, big-endian)"), the single most common mistake is reaching
for `ByteBuffer.putInt()`/`putShort()`/`putLong()` (or the matching `DataOutputStream`
methods) for a field whose declared width doesn't exactly match that method's native
write width. These methods always write their FULL native width - `putInt()` always
writes 4 bytes, `putShort()` always writes 2, regardless of what the wire format
actually specifies for that field. Confirmed live, repeatedly: a 3-byte `dataLength`
field written via `buffer.putInt(dataLength)` throws `java.nio.BufferOverflowException`
the instant the destination buffer is sized to the wire format's real total length
(e.g. a declared 9-byte header) rather than to what the 4-byte `putInt()` call actually
needs.

## The fix: manual byte-by-byte packing for any narrower-than-native field

For any field whose declared width is LESS than its natural Java primitive's width,
write (and read) each byte individually via bit-shifting and masking instead of a
single `putX()`/`getX()` call.

**Encode** (a 3-byte big-endian `int` field at offset `o` in a `byte[] header`):
```java
header[o]     = (byte) ((value >> 16) & 0xFF);
header[o + 1] = (byte) ((value >> 8)  & 0xFF);
header[o + 2] = (byte) (value & 0xFF);
```

**Decode** (the mirror image - mask each byte with `& 0xFF` FIRST, since Java `byte` is
signed and a raw byte with its high bit set would otherwise sign-extend into a
negative int when shifted):
```java
int value = ((data[o] & 0xFF) << 16) | ((data[o + 1] & 0xFF) << 8) | (data[o + 2] & 0xFF);
```

This generalizes directly:
- **1-byte field** (encode): `header[o] = (byte) (value & 0xFF);` (decode): `int value = data[o] & 0xFF;`
- **6-byte field holding a `long`**: the same pattern, just 6 shift/mask pairs (`>> 40`, `>> 32`, ..., `>> 0`) instead of 3.
- **Little-endian**: reverse which byte gets which shift amount (the least-significant byte goes first).

## When ByteBuffer's own putX()/getX() methods ARE fine

Only reach for `ByteBuffer.putInt()`/`getInt()` (or `putShort`/`getShort`,
`putLong`/`getLong`) directly when the wire field's declared width EXACTLY matches that
method's native width - a genuine 4-byte int field, a genuine 2-byte short field, a
genuine 8-byte long field. The manual byte-packing pattern above is only needed for the
mismatch case.

## Buffer/array sizing

Size the destination `byte[]`/`ByteBuffer` to the wire format's **exact** declared
total length - the sum of each field's own declared width (e.g. a 9-byte header really
means 9 total bytes for those fields, even though naively summing each field's native
Java primitive size would total more, e.g. 1 + 1 + 4 + 4 = 10 for the `dataLength`
example above if you mistakenly assumed it needs the full 4-byte int width). Never
derive buffer size from native primitive widths - derive it from the wire format's own
stated byte counts.

See `examples/WireProtocolCodec.java` for a complete, self-contained encode/decode
pair using this pattern across a 1-byte, a 3-byte, and a 4-byte field in the same
header.
