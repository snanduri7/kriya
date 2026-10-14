/*
 * EVALUATOR_AUTHORED_BEHAVIORAL_COVERAGE (MODEL-ROUTING-V1, task MR-J03).
 * Reason: the upstream tests only consume a promoted member name through nextString(), while the task requires that a map key read by
 * promoting a member name to a value updates getPath() and getPreviousPath() once the key has been consumed. This test checks that
 * requirement for the other public numeric accessors. It asserts observable reader behaviour only.
 */
package com.google.gson.stream;

import static com.google.common.truth.Truth.assertThat;

import com.google.gson.JsonElement;
import com.google.gson.internal.JsonReaderInternalAccess;
import com.google.gson.internal.Streams;
import com.google.gson.internal.bind.JsonTreeReader;
import java.io.IOException;
import java.io.StringReader;
import java.util.Arrays;
import java.util.List;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.Parameterized;

@SuppressWarnings("resource")
@RunWith(Parameterized.class)
public class JsonReaderPromotedKeyPathTest {
  @Parameterized.Parameters(name = "{0}")
  public static List<Object[]> parameters() {
    return Arrays.asList(
        new Object[] {Factory.STRING_READER}, new Object[] {Factory.OBJECT_READER});
  }

  @Parameterized.Parameter public Factory factory;

  private enum Accessor {
    INT,
    LONG,
    DOUBLE
  }

  /** Promotes the only member name to a value, consumes it with {@code accessor}, checks both paths. */
  private void checkPromotedKey(Accessor accessor, String key) throws IOException {
    JsonReader reader = factory.create("{\"" + key + "\":true}");
    reader.beginObject();
    JsonReaderInternalAccess.INSTANCE.promoteNameToValue(reader);
    switch (accessor) {
      case INT:
        assertThat((double) reader.nextInt()).isEqualTo(Double.parseDouble(key));
        break;
      case LONG:
        assertThat((double) reader.nextLong()).isEqualTo(Double.parseDouble(key));
        break;
      case DOUBLE:
        assertThat(reader.nextDouble()).isEqualTo(Double.parseDouble(key));
        break;
    }
    String expected = "$." + key;
    assertThat(reader.getPreviousPath()).isEqualTo(expected);
    assertThat(reader.getPath()).isEqualTo(expected);
    assertThat(reader.nextBoolean()).isTrue();
    assertThat(reader.getPreviousPath()).isEqualTo(expected);
    assertThat(reader.getPath()).isEqualTo(expected);
    reader.endObject();
  }

  @Test
  public void promotedNumericNamesUpdatePath() throws IOException {
    // Keys that are plain numbers, consumed through each numeric accessor (the usual Map<Integer, ...> etc.).
    checkPromotedKey(Accessor.INT, "12");
    checkPromotedKey(Accessor.LONG, "4000000000");
    checkPromotedKey(Accessor.DOUBLE, "1.5");
    checkPromotedKey(Accessor.DOUBLE, "7");
    if (factory == Factory.STRING_READER) {
      // JsonReader also accepts integral values written in floating point notation through nextInt()/nextLong().
      checkPromotedKey(Accessor.INT, "2.0");
      checkPromotedKey(Accessor.LONG, "1e3");
    }
  }

  public enum Factory {
    STRING_READER {
      @Override
      public JsonReader create(String data) {
        return new JsonReader(new StringReader(data));
      }
    },
    OBJECT_READER {
      @Override
      public JsonReader create(String data) {
        JsonElement element = Streams.parse(new JsonReader(new StringReader(data)));
        return new JsonTreeReader(element);
      }
    };

    abstract JsonReader create(String data);
  }
}
