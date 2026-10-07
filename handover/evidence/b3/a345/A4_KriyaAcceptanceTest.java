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
package org.apache.commons.cli;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class KriyaAcceptanceTest {
    private static CommandLine parse(final String... args) throws ParseException {
        final Options options = new Options();
        options.addOption("a", "alpha", false, "option a");
        options.addOption("b", false, "option b");
        return new DefaultParser().parse(options, args);
    }

    // kriya_requirement: REQ-1
    @Test
    void trueWhenAtLeastOneGivenNameIsPresent() throws ParseException {
        assertTrue(parse("-a").hasAnyOption("b", "a"));
    }

    // kriya_requirement: REQ-1
    @Test
    void trueWhenFirstNameIsPresentAndLaterNameIsAbsent() throws ParseException {
        assertTrue(parse("-a").hasAnyOption("a", "x"));
    }

    // kriya_requirement: REQ-1
    @Test
    void decidesExactlyAsHasOption() throws ParseException {
        final CommandLine line = parse("-a");
        assertEquals(line.hasOption("alpha"), line.hasAnyOption("alpha"));
    }

    // kriya_requirement: REQ-1
    @Test
    void falseWhenNoneIsPresent() throws ParseException {
        assertFalse(parse("-a").hasAnyOption("b", "x"));
    }

    // kriya_requirement: REQ-1
    @Test
    void falseWhenNoNamesAreGiven() throws ParseException {
        assertFalse(parse("-a").hasAnyOption());
    }
}
