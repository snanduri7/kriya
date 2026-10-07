# A3 post-P3-D attempt 0: harness invocation error, not a Kriya run

`run-p3d.sh b3-a3-java-lang goals/b3-a3-java-lang.txt` - the goal path was RELATIVE; the runner cd's into the workspace
before `kriya generate -f`, so Click refused the option (`Path 'goals/b3-a3-java-lang.txt' does not exist`, exit 2,
0.7 s). MEASURED: no run id, 0 model calls, workspace HEAD = frozen base, no tracked change. `analyze` ran (2 s).
Owner of the artifact: the measurement harness (my invocation). Not counted as the live run; re-invoked with the
absolute goal path (the form the B3 A3 run used).
