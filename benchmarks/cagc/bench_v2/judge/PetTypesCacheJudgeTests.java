package org.springframework.samples.petclinic.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Collection;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.samples.petclinic.model.PetType;
import org.springframework.samples.petclinic.model.Vet;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.junit.jupiter.SpringJUnitConfig;

/** Held-out judge (live closure matrix): pet types are cached like vets, in the application's cache configuration. */
@SpringJUnitConfig(locations = {"classpath:spring/business-config.xml", "classpath:spring/tools-config.xml"})
@ActiveProfiles("jpa")
class PetTypesCacheJudgeTests {

    @Autowired
    private ClinicService clinicService;

    @Test
    void repeatedPetTypeLookupsAreServedFromTheCache() {
        Collection<PetType> first = clinicService.findPetTypes();
        assertThat(first).isNotEmpty();
        assertThat(clinicService.findPetTypes()).isSameAs(first);
    }

    @Test
    void vetsAreStillCached() {
        Collection<Vet> first = clinicService.findVets();
        assertThat(clinicService.findVets()).isSameAs(first);
    }
}
