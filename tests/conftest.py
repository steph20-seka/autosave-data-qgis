# -*- coding: utf-8 -*-
"""Modules QGIS/Qt simulés pour tester la logique du plugin hors de QGIS."""
import os
import sys
import types
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_qgz(path, text="v0"):
    """Écrit un .qgz minimal et valide (archive contenant un .qgs)."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("projet.qgs", "<qgis>{}</qgis>".format(text))


class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        if slot not in self.slots:
            raise TypeError("not connected")
        self.slots.remove(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class FakeTimer:
    instances = []
    single_shots = []

    @staticmethod
    def singleShot(ms, callback):
        FakeTimer.single_shots.append((ms, callback))

    def __init__(self, parent=None):
        self.timeout = FakeSignal()
        self.single_shot = False
        self.interval = None
        self.active = False
        FakeTimer.instances.append(self)

    def setSingleShot(self, flag):
        self.single_shot = flag

    def start(self, ms):
        self.interval = ms
        self.active = True

    def stop(self):
        self.active = False

    def isActive(self):
        return self.active

    def fire(self):
        if self.single_shot:
            self.active = False
        self.timeout.emit()


class FakeAction:
    def __init__(self, *args):
        self.toggled = FakeSignal()
        self.triggered = FakeSignal()
        self.checked = False
        self._blocked = False

    def setCheckable(self, flag):
        pass

    def setChecked(self, flag):
        self.checked = flag

    def blockSignals(self, flag):
        previous = self._blocked
        self._blocked = flag
        return previous

    def setToolTip(self, text):
        pass


class FakeLabel:
    """Remplace le bouton d'état (QToolButton) : texte, info-bulle, signal clicked."""

    def __init__(self, text=""):
        self._text = text
        self.tooltip = ""
        self.deleted = False
        self.clicked = FakeSignal()

    def setAutoRaise(self, flag):
        pass

    def setContentsMargins(self, *args):
        pass

    def setText(self, text):
        self._text = text

    def text(self):
        return self._text

    def setToolTip(self, text):
        self.tooltip = text

    def deleteLater(self):
        self.deleted = True


class FakeProject:
    _instance = None

    def __init__(self):
        self.dirtySet = FakeSignal()
        self.isDirtyChanged = FakeSignal()
        self.cleared = FakeSignal()
        self.readProject = FakeSignal()
        self.projectSaved = FakeSignal()
        self.filename = ""
        self.dirty = False
        self.layers = {}
        self.writes = 0
        self.write_ok = True
        self.write_raises = False
        self.corrupt_on_write = False
        self.write_payload = None
        self.dirty_calls = []

    @classmethod
    def instance(cls):
        return cls._instance

    def fileName(self):
        return self.filename

    def isDirty(self):
        return self.dirty

    def mapLayers(self):
        return self.layers

    def error(self):
        return "erreur simulée"

    def write(self):
        if self.write_raises:
            raise RuntimeError("boom")
        self.writes += 1
        if self.filename and self.write_ok:
            make_qgz(self.filename, "v{}".format(self.writes))
        if self.write_payload is not None and self.filename:
            with open(self.filename, "wb") as handle:
                handle.write(self.write_payload)
        if self.corrupt_on_write and self.filename:
            with open(self.filename, "wb") as handle:
                handle.write(b"PK\x03\x04 tronque")
        if self.write_ok:
            self.dirty = False
            self.projectSaved.emit()
        return self.write_ok

    def setDirty(self, flag=True):
        self.dirty_calls.append(flag)
        self.dirty = flag
        if flag:
            self.dirtySet.emit()


class FakeVectorLayer:
    def __init__(self, name, editable=False):
        self._name = name
        self.editable = editable

    def name(self):
        return self._name

    def isEditable(self):
        return self.editable


class FakeTaskManager:
    active = 0

    def countActiveTasks(self):
        return FakeTaskManager.active


class FakeIface:
    def __init__(self):
        self.status = []
        self.bar = []
        self.menu = []
        self.toolbar = []
        self.permanent = []
        self.opened = []
        self._status_iface = None

    def addProject(self, path):
        self.opened.append(path)
        return True

    def mainWindow(self):
        return None

    def addToolBarIcon(self, action):
        self.toolbar.append(action)

    def removeToolBarIcon(self, action):
        self.toolbar.remove(action)

    def addPluginToMenu(self, menu, action):
        self.menu.append(action)

    def removePluginMenu(self, menu, action):
        self.menu.remove(action)

    def statusBarIface(self):
        outer = self
        if self._status_iface is None:
            class Status:
                def showMessage(self, text, timeout=0):
                    outer.status.append(text)

                def addPermanentWidget(self, widget):
                    outer.permanent.append(widget)

                def removeWidget(self, widget):
                    outer.permanent.remove(widget)
            self._status_iface = Status()
        return self._status_iface

    def messageBar(self):
        outer = self

        class Bar:
            def pushMessage(self, title, text, level=None, duration=0):
                outer.bar.append((title, text, level))
        return Bar()


class FakeSettings:
    store = {}

    def value(self, key, default=None):
        return FakeSettings.store.get(key, default)

    def setValue(self, key, value):
        FakeSettings.store[key] = value


class FakeProfile:
    path = None


class FakeLog:
    messages = []

    @staticmethod
    def logMessage(text, tag="", level=None):
        FakeLog.messages.append((text, tag, level))


class FakeQApp:
    aboutToQuit = FakeSignal()


class FakeApp:
    modal = None

    @staticmethod
    def activeModalWidget():
        return FakeApp.modal


def _install_stubs():
    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        sys.modules[name] = mod
        return mod

    class Qgis:  # style QGIS 3.22 : constantes directes
        Info, Warning, Critical, Success = 0, 1, 2, 3

    module("qgis")
    module("qgis.PyQt")
    module("qgis.PyQt.QtCore", QTimer=FakeTimer, QUrl=type("QUrl", (), {
        "fromLocalFile": staticmethod(lambda p: ("url", p))}))
    module("qgis.PyQt.QtGui",
           QIcon=lambda *a: None,
           QDesktopServices=type("QDesktopServices", (), {
               "opened": [],
               "openUrl": staticmethod(lambda url: True)}))
    module("qgis.PyQt.QtWidgets", QAction=FakeAction, QApplication=FakeApp,
           QToolButton=FakeLabel)
    module("qgis.core", Qgis=Qgis, QgsApplication=type(
        "QgsApplication", (), {
            "taskManager": staticmethod(lambda: FakeTaskManager()),
            "qgisSettingsDirPath": staticmethod(lambda: FakeProfile.path),
            "instance": staticmethod(lambda: FakeQApp)}),
        QgsMessageLog=FakeLog, QgsProject=FakeProject, QgsSettings=FakeSettings,
        QgsVectorLayer=FakeVectorLayer)


_install_stubs()


@pytest.fixture
def env(tmp_path):
    FakeTimer.instances = []
    FakeTimer.single_shots = []
    FakeQApp.aboutToQuit = FakeSignal()
    FakeProfile.path = str(tmp_path / "_profil")
    FakeSettings.store = {}
    FakeLog.messages = []
    FakeApp.modal = None
    FakeTaskManager.active = 0
    FakeProject._instance = FakeProject()

    project_file = tmp_path / "carte.qgz"
    make_qgz(str(project_file), "v0")

    from autosave_data import plugin as plugin_module
    iface = FakeIface()
    plug = plugin_module.AutoSaveDataPlugin(iface)
    plug.initGui()

    class Env:
        pass

    e = Env()
    e.plugin = plug
    e.iface = iface
    e.project = FakeProject._instance
    e.file = project_file
    e.dir = tmp_path
    e.debounce = plug._debounce
    e.safety = plug._safety
    e.layers = FakeVectorLayer
    e.module = plugin_module
    return e
