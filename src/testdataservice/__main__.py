"""Module entry point: ``python -m testdataservice``.

Equivalent to the ``tds`` console script, but launched through the interpreter
directly — handy where the generated script shim isn't on PATH.
"""

from testdataservice.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
