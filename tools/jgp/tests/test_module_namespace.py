"""Package imports must coexist with Flickornas modules of the same name.

A fresh interpreter prevents cached imports from hiding namespace collisions.
"""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_TYPST = ROOT / "reference-artifacts" / "2026-standings-golden.typ"

# The top-level module names Flickornas installs that JGP's modules used to share.
FLICKORNAS_NAMES = (
    "points", "active_configuration", "artifact_publication", "atomic_write", "generation_core",
    "generation_orchestrator", "generation_progress", "klass_naming", "typst_rules",
)


def _run(script: str, cwd: Path) -> subprocess.CompletedProcess:
    environment = {name: value for name, value in os.environ.items() if name != "JGP_FIXTURES_DIR"}
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=cwd, capture_output=True, text=True, check=False, env=environment,
    )


def test_a_generation_beside_stand_ins_for_the_colliding_modules_reproduces_the_golden_source(tmp_path):
    stand_ins = tmp_path / "flickornas"
    stand_ins.mkdir()
    for name in FLICKORNAS_NAMES:
        (stand_ins / f"{name}.py").write_text(f"OWNER = 'flickornas {name}'\n")
    working_directory = tmp_path / "cwd"
    working_directory.mkdir()

    result = _run(f"""
        import sys
        sys.path.insert(0, {str(stand_ins)!r})
        names = {FLICKORNAS_NAMES!r}
        for name in names:
            __import__(name)

        from datetime import date
        from pathlib import Path
        from jgp_standings.active_configuration import load_configuration
        from jgp_standings.artifact_publication import TYPST_SOURCE_FILE, generate_and_publish
        from jgp_standings.upstream_client import FixtureReplay

        class NoRenderer:
            def render(self, source, destination, *, root, font_directory):
                destination.write_bytes(b"%PDF-")

        published = generate_and_publish(
            load_configuration(Path({str(ROOT / "state")!r})),
            Path({str(tmp_path / "output")!r}),
            fetch=FixtureReplay(Path({str(ROOT / "fixtures" / "2026")!r})),
            state_directory=Path({str(tmp_path / "state")!r}),
            today=date(2026, 8, 2),
            renderer=NoRenderer(),
        )
        for name in names:
            assert sys.modules[name].OWNER == f"flickornas {{name}}", name
        print(published.run_directory / TYPST_SOURCE_FILE)
    """, cwd=working_directory)

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).read_bytes() == GOLDEN_TYPST.read_bytes()
    assert list(working_directory.iterdir()) == []


def test_importing_the_manifest_touches_no_files_and_starts_no_services(tmp_path):
    result = _run(f"""
        import os, sys, threading
        accessed = []
        def audit(event, arguments):
            if event in ("socket.connect", "subprocess.Popen"):
                accessed.append(event)
            elif event == "open" and isinstance(arguments[0], str) and not arguments[0].endswith((".py", ".pyc", ".so")):
                if not os.path.isabs(arguments[0]) or arguments[0].startswith({str(ROOT)!r}):
                    accessed.append(arguments[0])
        sys.addaudithook(audit)

        from jgp_standings.web import MANIFEST
        assert MANIFEST["url_prefix"] == "/tools/jgp"
        assert accessed == [] and threading.active_count() == 1, (accessed, threading.enumerate())
    """, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
