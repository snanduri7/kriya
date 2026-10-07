/*
  Licensed to the Apache Software Foundation (ASF) under one or more
  contributor license agreements.  See the NOTICE file distributed with
  this work for additional information regarding copyright ownership.
  The ASF licenses this file to You under the Apache License, Version 2.0
  (the "License"); you may not use this file except in compliance with
  the License.  You may obtain a copy of the License at

      https://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
 */
package org.apache.commons.lang3.math;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

class KriyaAcceptanceTest {
    // kriya_requirement: REQ-1
    @Test
    void returnsMinWhenValueIsLessThanMin() {
        assertEquals(0, NumberUtils.clamp(-5, 0, 10));
    }

    // kriya_requirement: REQ-1
    @Test
    void returnsMaxWhenValueIsGreaterThanMax() {
        assertEquals(10, NumberUtils.clamp(15, 0, 10));
    }

    // kriya_requirement: REQ-1
    @Test
    void returnsValueOtherwise() {
        assertEquals(5, NumberUtils.clamp(5, 0, 10));
    }

    // kriya_requirement: REQ-1
    @Test
    void throwsIllegalArgumentExceptionWhenMinIsGreaterThanMax() {
        assertThrows(IllegalArgumentException.class, () -> NumberUtils.clamp(1, 10, 0));
    }
}
