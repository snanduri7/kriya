/*
 * Licensed to the Apache Software Foundation (ASF) under one or more contributor license agreements. Independent check (scratch only).
 */
package org.springframework.samples.petclinic.web;

import static org.junit.jupiter.api.Assertions.*;

import java.text.ParseException;
import java.util.List;
import java.util.Locale;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.samples.petclinic.model.PetType;
import org.springframework.samples.petclinic.service.ClinicService;

class IndepCheckTest {
    private static PetType t(String n) { PetType p = new PetType(); p.setName(n); return p; }

    @Test
    void independentCases() throws Exception {
        ClinicService s = Mockito.mock(ClinicService.class);
        Mockito.when(s.findPetTypes()).thenReturn(List.of(t("Dog"), t("Bird"), t("Hamster"), t("Lizard")));
        PetTypeFormatter f = new PetTypeFormatter(s);
        assertEquals("Bird", f.parse("  bird ", Locale.ENGLISH).getName());
        assertEquals("Bird", f.parse("\tBIRD\n", Locale.ENGLISH).getName());
        assertEquals("Dog", f.parse(" dOg ", Locale.ENGLISH).getName());
        assertEquals("Hamster", f.parse(" hAmStEr  ", Locale.ENGLISH).getName());
        assertEquals("Lizard", f.parse("Lizard", Locale.ENGLISH).getName());
        assertThrows(ParseException.class, () -> f.parse("Fish", Locale.ENGLISH));
        assertThrows(ParseException.class, () -> f.parse("  fish ", Locale.ENGLISH));
        assertThrows(ParseException.class, () -> f.parse("Bir d", Locale.ENGLISH));
        System.out.println("INDEPENDENT CHECK: all cases PASS");
    }
}
