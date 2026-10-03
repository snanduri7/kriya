| task | arm | rep | run id | outcome | s | targets | T0 | repairs | retries | rules sent | fit drops | dev prompt tokens | dev prefill s | judge | diff |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| java-behavior-accents | A | r1 | 20261003T135916-d568a487 | SUCCESS | 940 | yes | yes | 1 | 0 | - | - | 13672 | 21.052 | SOLVED | yes |
| java-behavior-accents | B | r2 | 20261003T141937-db6a560c | SUCCESS | 818 | yes | yes | 0 | 4 | java.dev.import_style, maven.plan.existing_topology_preserved | - | 30411 | 38.44 | SOLVED | yes |
| java-behavior-accents | B | r3 | 20261003T143416-0a32fc0f | SUCCESS | 1154 | yes | yes | 0 | 2 | java.dev.import_style, maven.plan.existing_topology_preserved | - | 18055 | 23.018 | SOLVED | yes |
| java-behavior-accents | A | r4 | 20261003T145400-99189c52 | FAILURE | 1791 | yes | yes | 0 | 5 | - | - | 38014 | 55.561 | NOT_SOLVED | no |
| java-symbol-chop | A | r1 | 20261003T152912-b048853d | FAILURE | 1344 | yes | yes | 1 | 4 | - | - | 38166 | 53.272 | NOT_SOLVED | no |
| java-symbol-chop | B | r2 | 20261003T155704-f6a44a17 | SUCCESS | 1323 | yes | yes | 1 | 3 | java.dev.import_style, maven.plan.existing_topology_preserved | java.dev.import_style | 29831 | 39.016 | SOLVED | yes |
| java-symbol-chop | B | r3 | 20261003T161941-0ac7e929 | FAILURE | 1164 | yes | yes | 1 | 4 | java.dev.import_style, maven.plan.existing_topology_preserved | java.dev.import_style | 38847 | 62.519 | NOT_SOLVED | no |
| java-symbol-chop | A | r4 | 20261003T163942-3efef59f | SUCCESS | 898 | yes | yes | 0 | 1 | - | - | 17304 | 26.993 | SOLVED | yes |
| java-symbol-fraction | A | r1 | 20261003T123740-ff84f336 | FAILURE | 990 | no | yes | 1 | 0 | - | - | 7191 | 11.252 | SOLVED | no |
| java-symbol-fraction | B | r2 | 20261003T125944-07ef5770 | SUCCESS | 1229 | no | yes | 0 | 2 | java.dev.import_style, maven.plan.existing_topology_preserved | - | 29813 | 44.758 | SOLVED | yes |
| java-symbol-fraction | B | r3 | 20261003T132049-0e59f850 | FAILURE | 751 | no | yes | 0 | 1 | java.dev.import_style, maven.plan.existing_topology_preserved | - | 12098 | 18.511 | SOLVED | no |
| java-symbol-fraction | A | r4 | 20261003T133357-7ef04b4f | FAILURE | 1238 | no | yes | 1 | 1 | - | - | 28004 | 46.139 | SOLVED | no |
| python-behavior-maxsplit | A | r1 | 20261003T174932-5df2868d | FAILURE | 790 | yes | yes | 2 | 6 | - | - | 25930 | 41.096 | NOT_SOLVED | no |
| python-behavior-maxsplit | B | r2 | 20261003T180332-cf817c8c | FAILURE | 948 | yes | yes | 1 | 4 | - | - | 18138 | 36.592 | NOT_SOLVED | no |
| python-behavior-maxsplit | B | r3 | 20261003T181927-ab89c7ce | FAILURE | 434 | yes | yes | 2 | 4 | - | - | 24379 | 28.644 | NOT_SOLVED | no |
| python-behavior-maxsplit | A | r4 | 20261003T182647-d0ef4e13 | FAILURE | 634 | yes | yes | 0 | 5 | - | - | 27560 | 58.605 | NOT_SOLVED | no |
| python-error-invalidurl | A | r1 | 20261003T195829-feea3bae | FAILURE | 85 | no | yes | 0 | 3 | - | - | 5058 | 7.905 | NOT_SOLVED | no |
| python-error-invalidurl | B | r2 | 20261003T200036-7b2748ba | FAILURE | 115 | no | yes | 2 | 3 | - | - | 6888 | 13.444 | NOT_SOLVED | no |
| python-error-invalidurl | B | r3 | 20261003T200234-478d4205 | FAILURE | 98 | no | yes | 2 | 0 | - | - | 0 | 0 | NOT_SOLVED | no |
| python-error-invalidurl | A | r4 | 20261003T200416-a5b1cf7e | FAILURE | 111 | no | yes | 2 | 3 | - | - | 6860 | 13.213 | NOT_SOLVED | no |
| python-symbol-ichunked | A | r1 | 20261003T191357-b4f69699 | FAILURE | 259 | yes | yes | 2 | 0 | - | - | 7266 | 17.793 | NOT_SOLVED | no |
| python-symbol-ichunked | B | r2 | 20261003T191903-34834be5 | FAILURE | 257 | yes | yes | 2 | 0 | - | - | 6330 | 15.288 | NOT_SOLVED | no |
| python-symbol-ichunked | B | r3 | 20261003T192327-6eeba02e | FAILURE | 215 | yes | yes | 1 | 0 | - | - | 7770 | 17.623 | NOT_SOLVED | no |
| python-symbol-ichunked | A | r4 | 20261003T192709-8d4a0cb3 | SUCCESS | 431 | yes | yes | 1 | 1 | - | - | 21213 | 45.213 | SOLVED | yes |
| python-symbol-one | A | r1 | 20261003T183804-ac1eaaf1 | FAILURE | 554 | no | yes | 1 | 6 | - | - | 43849 | 73.713 | NOT_SOLVED | no |
| python-symbol-one | B | r2 | 20261003T184803-848cb1a6 | FAILURE | 542 | no | yes | 1 | 5 | - | - | 38283 | 62.759 | NOT_SOLVED | no |
| python-symbol-one | B | r3 | 20261003T185711-dd6c085c | FAILURE | 493 | no | yes | 1 | 5 | - | - | 38354 | 66.113 | NOT_SOLVED | no |
| python-symbol-one | A | r4 | 20261003T190530-7ac10f44 | FAILURE | 462 | no | yes | 2 | 4 | - | - | 31739 | 54.919 | NOT_SOLVED | no |
| python-symbol-zipb | A | r1 | 20261003T193459-ee4cdc04 | FAILURE | 482 | yes | yes | 1 | 0 | - | - | 3941 | 6.815 | NOT_SOLVED | no |
| python-symbol-zipb | B | r2 | 20261003T194344-7b63e06f | FAILURE | 220 | yes | yes | 1 | 1 | - | - | 9736 | 15.651 | NOT_SOLVED | no |
| python-symbol-zipb | B | r3 | 20261003T194732-5e8bd9d8 | FAILURE | 244 | yes | yes | 2 | 1 | - | - | 10044 | 17.593 | NOT_SOLVED | no |
| python-symbol-zipb | A | r4 | 20261003T195143-321cd89c | FAILURE | 363 | yes | yes | 1 | 2 | - | - | 13331 | 24.35 | NOT_SOLVED | no |
| spring-boot-pagesize | A | r1 | 20261003T165535-fd260d70 | FAILURE | 543 | no | yes | 0 | 3 | - | - | 33377 | 49.933 | NOT_SOLVED | no |
| spring-boot-pagesize | B | r2 | 20261003T170523-cdd88fcb | FAILURE | 531 | no | yes | 0 | 2 | gradle.plan.existing_topology_preserved, java.dev.import_style, maven.plan.existing_topology_preserved, spring.review.transactional_self_invocation | - | 27085 | 40.575 | NOT_SOLVED | no |
| spring-boot-pagesize | B | r3 | 20261003T171432-a6a362dd | FAILURE | 423 | no | yes | 0 | 2 | gradle.plan.existing_topology_preserved, java.dev.import_style, maven.plan.existing_topology_preserved, spring.review.transactional_self_invocation | - | 25936 | 40.638 | NOT_SOLVED | no |
| spring-boot-pagesize | A | r4 | 20261003T172152-22cf5a62 | FAILURE | 459 | no | yes | 1 | 2 | - | - | 33723 | 61.99 | NOT_SOLVED | no |
| spring-xml-pettypes-cache | A | r1 | 20261003T173013-6fbc831a | SUCCESS | 353 | no | yes | 1 | 0 | - | - | 9989 | 19.869 | SOLVED | yes |
| spring-xml-pettypes-cache | B | r2 | 20261003T173645-e11e227e | SUCCESS | 358 | no | yes | 0 | 2 | java.dev.import_style, maven.plan.existing_topology_preserved, spring.review.transactional_self_invocation | - | 24510 | 42.562 | SOLVED | yes |
| spring-xml-pettypes-cache | B | r3 | 20261003T174257-67b6c0a6 | FAILURE | 76 | no | yes | 0 | 0 | maven.plan.existing_topology_preserved | - | 0 | 0 | NOT_SOLVED | no |
| spring-xml-pettypes-cache | A | r4 | 20261003T174428-d11c6d2b | SUCCESS | 254 | no | yes | 1 | 0 | - | - | 6631 | 10.156 | SOLVED | yes |

```json
{
  "arms": {
    "A": {
      "correct_target_rate": 0.5,
      "developer_retries": {
        "0": 6,
        "1": 3,
        "2": 2,
        "3": 3,
        "4": 2,
        "5": 2,
        "6": 2
      },
      "exact_t0_rate": 1.0,
      "false_success": 0,
      "judge_verified_denominator": 16,
      "judge_verified_success": 5,
      "judge_verified_success_rate": 0.3125,
      "kriya_success": 5,
      "kriya_success_rate": 0.25,
      "planner_repairs": {
        "0": 5,
        "1": 11,
        "2": 4
      },
      "runs": 20,
      "success_not_independently_verifiable": 0
    },
    "B": {
      "correct_target_rate": 0.5,
      "developer_retries": {
        "0": 4,
        "1": 3,
        "2": 5,
        "3": 2,
        "4": 4,
        "5": 2
      },
      "exact_t0_rate": 1.0,
      "false_success": 0,
      "judge_verified_denominator": 16,
      "judge_verified_success": 4,
      "judge_verified_success_rate": 0.25,
      "kriya_success": 5,
      "kriya_success_rate": 0.25,
      "planner_repairs": {
        "0": 8,
        "1": 7,
        "2": 5
      },
      "runs": 20,
      "success_not_independently_verifiable": 1
    }
  },
  "guidance": {
    "developer_median_estimated_tokens": 33.5,
    "leakage": [],
    "planner_fit_drop_rate": 0.0,
    "planner_requests": 37,
    "requests": 132,
    "requests_with_cap_drop": 0,
    "requests_with_fit_drop": 3
  },
  "judges": {
    "java-behavior-accents": "DISCRIMINATING",
    "java-symbol-chop": "DISCRIMINATING",
    "java-symbol-fraction": "NON_DISCRIMINATING",
    "python-behavior-maxsplit": "DISCRIMINATING",
    "python-error-invalidurl": "DISCRIMINATING",
    "python-symbol-ichunked": "DISCRIMINATING",
    "python-symbol-one": "AMBIGUOUS",
    "python-symbol-zipb": "DISCRIMINATING",
    "spring-boot-pagesize": "DISCRIMINATING",
    "spring-xml-pettypes-cache": "DISCRIMINATING"
  },
  "per_task": {
    "java-behavior-accents": {
      "developer_prefill_seconds": {
        "A": 38.3,
        "B": 30.7
      },
      "developer_prompt_eval_tokens": {
        "A": 25843,
        "B": 24233,
        "delta": -1610
      },
      "guidance_applicable": true,
      "judge": "DISCRIMINATING",
      "prompt_gate": "PASS"
    },
    "java-symbol-chop": {
      "developer_prefill_seconds": {
        "A": 40.1,
        "B": 50.8
      },
      "developer_prompt_eval_tokens": {
        "A": 27735,
        "B": 34339,
        "delta": 6604
      },
      "guidance_applicable": true,
      "judge": "DISCRIMINATING",
      "prompt_gate": "FAIL"
    },
    "java-symbol-fraction": {
      "developer_prefill_seconds": {
        "A": 28.7,
        "B": 31.6
      },
      "developer_prompt_eval_tokens": {
        "A": 17597.5,
        "B": 20955.5,
        "delta": 3358.0
      },
      "guidance_applicable": true,
      "judge": "NON_DISCRIMINATING",
      "prompt_gate": "FAIL"
    },
    "python-behavior-maxsplit": {
      "developer_prefill_seconds": {
        "A": 49.9,
        "B": 32.6
      },
      "developer_prompt_eval_tokens": {
        "A": 26745,
        "B": 21258.5,
        "delta": -5486.5
      },
      "guidance_applicable": false,
      "judge": "DISCRIMINATING",
      "prompt_gate": "PASS"
    },
    "python-error-invalidurl": {
      "developer_prefill_seconds": {
        "A": 10.6,
        "B": 6.7
      },
      "developer_prompt_eval_tokens": {
        "A": 5959,
        "B": 3444,
        "delta": -2515
      },
      "guidance_applicable": false,
      "judge": "DISCRIMINATING",
      "prompt_gate": "PASS"
    },
    "python-symbol-ichunked": {
      "developer_prefill_seconds": {
        "A": 31.5,
        "B": 16.5
      },
      "developer_prompt_eval_tokens": {
        "A": 14239.5,
        "B": 7050,
        "delta": -7189.5
      },
      "guidance_applicable": false,
      "judge": "DISCRIMINATING",
      "prompt_gate": "PASS"
    },
    "python-symbol-one": {
      "developer_prefill_seconds": {
        "A": 64.3,
        "B": 64.4
      },
      "developer_prompt_eval_tokens": {
        "A": 37794,
        "B": 38318.5,
        "delta": 524.5
      },
      "guidance_applicable": false,
      "judge": "AMBIGUOUS",
      "prompt_gate": "FAIL"
    },
    "python-symbol-zipb": {
      "developer_prefill_seconds": {
        "A": 15.6,
        "B": 16.6
      },
      "developer_prompt_eval_tokens": {
        "A": 8636,
        "B": 9890,
        "delta": 1254
      },
      "guidance_applicable": false,
      "judge": "DISCRIMINATING",
      "prompt_gate": "FAIL"
    },
    "spring-boot-pagesize": {
      "developer_prefill_seconds": {
        "A": 56.0,
        "B": 40.6
      },
      "developer_prompt_eval_tokens": {
        "A": 33550,
        "B": 26510.5,
        "delta": -7039.5
      },
      "guidance_applicable": true,
      "judge": "DISCRIMINATING",
      "prompt_gate": "PASS"
    },
    "spring-xml-pettypes-cache": {
      "developer_prefill_seconds": {
        "A": 15.0,
        "B": 21.3
      },
      "developer_prompt_eval_tokens": {
        "A": 8310,
        "B": 12255,
        "delta": 3945
      },
      "guidance_applicable": true,
      "judge": "DISCRIMINATING",
      "prompt_gate": "FAIL"
    }
  },
  "runtime_digests": {
    "A": [
      "0769fe6214918434eeb7658144e6821c2d936b9b20c1ef3c2d4de03000363437",
      "26cb2deb79ae43141f637b99510d292002f4174558d5a2b20d09c2d0a94bdfd3"
    ],
    "B": [
      "0769fe6214918434eeb7658144e6821c2d936b9b20c1ef3c2d4de03000363437",
      "26cb2deb79ae43141f637b99510d292002f4174558d5a2b20d09c2d0a94bdfd3"
    ]
  }
}
```
