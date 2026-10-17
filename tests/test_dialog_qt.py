# -*- coding: utf-8 -*-
"""Fenêtre de réglages vérifiée avec le vrai Qt (PyQt5 et/ou PyQt6 s'ils sont installés).

Le contrôle tourne dans un processus séparé : les autres tests remplacent les
modules Qt par des doublures.
"""
import importlib.util
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.parametrize("qt", ["PyQt5", "PyQt6"])
def test_settings_dialog_behaves_with_real_qt(qt):
    if importlib.util.find_spec(qt) is None:
        pytest.skip("{} n'est pas installé".format(qt))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        [sys.executable, "-I", os.path.join(HERE, "qt_dialog_check.py"), qt],
        capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "comportement OK" in result.stdout
