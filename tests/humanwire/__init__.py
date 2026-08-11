from pathlib import Path

# This test package shares a name with the source package. Point submodule
# resolution at the source tree so pytest can collect this package safely.
__path__ = [
    str(Path(__file__).parent),
    str(Path(__file__).parents[2] / "src" / "humanwire"),
]
