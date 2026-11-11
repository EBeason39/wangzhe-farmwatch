"""Keep all mutable test state outside the user's project data."""
import atexit
from dataclasses import replace
from pathlib import Path
import tempfile

from farmwatch.app import FarmApp
from farmwatch.config import ROOT, Settings


def make_app(settings=None):
    temporary = tempfile.TemporaryDirectory(prefix='farmwatch-test-')
    atexit.register(temporary.cleanup)
    settings = replace(settings or Settings(), root=Path(temporary.name), friends_enabled=False)
    app = FarmApp(settings)
    app.TEMPLATE_DIR = ROOT / 'resources' / 'ui' / '1280x720'
    return app
