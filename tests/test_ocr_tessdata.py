import os

from app.vision import text
from app.vision.text import ROOT, _engine


def test_missing_tessdata_dir_falls_back_to_project_models(monkeypatch):
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    _engine.cache_clear()
    cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if not os.path.exists(cmd):
        return
    _engine(cmd, r"F:\no\such\machine\tessdata")
    assert os.environ["TESSDATA_PREFIX"] == str((ROOT / "models" / "tessdata").resolve())
    _engine.cache_clear()
