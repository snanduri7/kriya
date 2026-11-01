Call-for-call equivalence of the requirement-closer sites (Gate F class-C consolidation). `closer_recorder.py.txt` is a scratch pytest plugin
(stored .txt so ruff/pytest ignore it) that wraps every `requirement_closers.close_requirements_*` and logs closer name + normalized
argument shapes/scalars per test. Same 84-test-recording file set run BEFORE (HEAD 89c7991, kriya/ unchanged: 669 passed) and AFTER the change
(710 passed = 669 + the 41 new Gate-F tests). Result: 84 tests, 2277 closer calls, ZERO differences in order, closer identity, argument shape or scalar value.
Not covered by the recorder: object-argument identity beyond type (by construction) and the thread an argument expression is evaluated on
(the five contract closers' argument expressions were evaluated on a worker thread before; they are now read on the calling thread - pure reads).
