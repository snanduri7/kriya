#!/bin/zsh
B2=$HOME/kriya-cagc-bench-v2; J=${0:A:h}/judge_v2.sh; M=$HOME/kriya-bench-live/matrix; BD=${0:A:h:h}
$J java-symbol-fraction base $B2/ws/java-symbol-fraction cab37e0c8 a0ffef035d6a0e7803da8405ebceacb011efbaa0 FractionTest
$J java-symbol-fraction ref  $B2/ws/java-symbol-fraction cab37e0c8 a0ffef035d6a0e7803da8405ebceacb011efbaa0 FractionTest
$J spring-boot-pagesize ref $M/ws/spring-boot-pagesize 500158f732419217507c7656904b8e6aa1bcc0d6 $BD/reference/spring-boot-pagesize.patch OwnerPageSizeJudgeTests $BD/judge/OwnerPageSizeJudgeTests.java src/test/java/org/springframework/samples/petclinic/owner
$J spring-xml-pettypes-cache ref $M/ws/spring-xml-pettypes-cache 09351b3ee0bd5aec2d480c0280e84700978f56d3 $BD/reference/spring-xml-pettypes-cache.patch PetTypesCacheJudgeTests $BD/judge/PetTypesCacheJudgeTests.java src/test/java/org/springframework/samples/petclinic/service
echo QUALIFICATION_COMPLETE
