"""Test configuration for the backend test suite.

Ensures the test runner can resolve ``app`` (backend root) and
``scripts/`` (top-level utilities like ``wizard`` and ``doctor``)
from any working directory.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# Make 'app' (backend/) and 'scripts/' (repo-root/scripts) importable
# regardless of the cwd pytest was invoked from.
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


@pytest.fixture()
def provisioner_module():
    """Load docker/provisioner/app.py as an importable test module.

    Shared by test_provisioner_kubeconfig and test_provisioner_pvc_volumes so
    that any change to the provisioner entry-point path or module name only
    needs to be updated in one place.
    """
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "docker" / "provisioner" / "app.py"
    spec = importlib.util.spec_from_file_location("provisioner_app_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
