package org.springframework.samples.petclinic.owner;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;

import java.util.List;

import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.DisabledInNativeImage;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.Pageable;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.aot.DisabledInAotMode;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/**
 * Held-out judge (live closure matrix): the owners page size is configurable, default 5.
 */
@DisabledInNativeImage
@DisabledInAotMode
class OwnerPageSizeJudgeTests {

	static int requestedPageSize(MockMvc mockMvc, OwnerRepository owners) throws Exception {
		given(owners.findByLastNameStartingWith(anyString(), org.mockito.ArgumentMatchers.any(Pageable.class)))
			.willReturn(new PageImpl<>(List.of()));
		mockMvc.perform(get("/owners").param("page", "1").param("lastName", "Franklin"));
		ArgumentCaptor<Pageable> pageable = ArgumentCaptor.forClass(Pageable.class);
		verify(owners).findByLastNameStartingWith(anyString(), pageable.capture());
		return pageable.getValue().getPageSize();
	}

	@Nested
	@WebMvcTest(OwnerController.class)
	@TestPropertySource(properties = "petclinic.owners.page-size=2")
	class Configured {

		@Autowired
		MockMvc mockMvc;

		@MockitoBean
		OwnerRepository owners;

		@Test
		void usesTheConfiguredPageSize() throws Exception {
			assertThat(requestedPageSize(mockMvc, owners)).isEqualTo(2);
		}

	}

	@Nested
	@WebMvcTest(OwnerController.class)
	class Default {

		@Autowired
		MockMvc mockMvc;

		@MockitoBean
		OwnerRepository owners;

		@Test
		void defaultsToFive() throws Exception {
			assertThat(requestedPageSize(mockMvc, owners)).isEqualTo(5);
		}

	}

}
