import subprocess
import sys


def test_package_imports_cleanly():
    import taikolab
    import taikolab.arranger
    import taikolab.exporters
    import taikolab.ingest
    import taikolab.ui

    assert taikolab is not None


def test_core_layers_stay_isolated_from_ml():
    """Guardrail: fresh import of arranger & exporters must never pull in heavy ML libs."""
    code = (
        "import sys; "
        "import taikolab.schemas, taikolab.config, "
        "taikolab.arranger.engine, taikolab.exporters.tja_writer, taikolab.exporters.diagnostics; "
        "bad = [m for m in ('torch', 'torchaudio', 'fastapi', 'librosa') if m in sys.modules]; "
        "assert not bad, f'Forbidden imports loaded: {bad}'"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, f"Isolation check failed:\n{proc.stderr}\n{proc.stdout}"