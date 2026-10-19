"""The ``kriya`` command groups moved out of ``kriya/cli.py`` (full modularization E14; TARGET-MODULE-MAP §3.4).

``kriya/cli.py`` keeps ``main`` and registers each group on it. A module here never imports ``kriya.cli`` or
``kriya.repl`` (the two form an import cycle; a command module that reached either would join it).
"""
