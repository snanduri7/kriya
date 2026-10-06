package org.springframework.samples.petclinic.web;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.text.ParseException;
import java.util.List;
import java.util.Locale;

import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.samples.petclinic.model.PetType;
import org.springframework.samples.petclinic.service.ClinicService;

class KriyaAcceptanceTest {
    private static PetTypeFormatter formatter() {
        final ClinicService service = Mockito.mock(ClinicService.class);
        final PetType dog = new PetType();
        dog.setName("Dog");
        final PetType bird = new PetType();
        bird.setName("Bird");
        Mockito.when(service.findPetTypes()).thenReturn(List.of(dog, bird));
        return new PetTypeFormatter(service);
    }

    // kriya_requirement: REQ-1
    @Test
    void parsesCaseInsensitivelyIgnoringSurroundingWhitespace() throws ParseException {
        assertEquals("Bird", formatter().parse("  bird ", Locale.ENGLISH).getName());
    }

    // kriya_requirement: REQ-1
    @Test
    void stillThrowsParseExceptionWhenNoPetTypeMatches() {
        assertThrows(ParseException.class, () -> formatter().parse("Fish", Locale.ENGLISH));
    }
}
