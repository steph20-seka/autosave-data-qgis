# -*- coding: utf-8 -*-
import os
from datetime import datetime

from autosave_data import core


def ev(**kw):
    base = dict(enabled=True, filename="/p/a.qgz", is_dirty=True, modal_open=False,
                writable=True, editing_layers=(), tasks_active=0,
                pause_while_editing=True, pause_during_tasks=True, manual=False)
    base.update(kw)
    return core.evaluate(**base)


def test_save_when_everything_is_clear():
    assert ev().action == core.SAVE


def test_disabled_and_no_file_and_clean_are_skipped():
    assert ev(enabled=False).reason == core.R_DISABLED
    assert ev(filename="").reason == core.R_NO_FILE
    assert ev(is_dirty=False).reason == core.R_CLEAN
    assert ev(writable=False).reason == core.R_READ_ONLY


def test_editing_layer_pauses_saving():
    d = ev(editing_layers=("routes", "bati"))
    assert (d.action, d.reason, d.detail) == (core.WAIT, core.R_EDITING, "routes, bati")


def test_editing_pause_can_be_turned_off():
    assert ev(editing_layers=("routes",), pause_while_editing=False).action == core.SAVE


def test_running_task_pauses_saving():
    assert ev(tasks_active=2).reason == core.R_TASKS
    assert ev(tasks_active=2, pause_during_tasks=False).action == core.SAVE


def test_modal_dialog_waits_even_when_manual():
    assert ev(modal_open=True).reason == core.R_MODAL
    assert ev(modal_open=True, manual=True).action == core.WAIT


def test_manual_save_ignores_editing_and_tasks_and_disabled():
    d = ev(manual=True, enabled=False, editing_layers=("a",), tasks_active=3)
    assert d.action == core.SAVE


def test_unknown_writability_does_not_block():
    assert ev(writable=None).action == core.SAVE


def test_config_clamping():
    cfg = core.Config(delay_seconds=1, max_wait_seconds=5, safety_minutes=999,
                      backups_keep=0, backups_min_interval_minutes=0).clamped()
    assert cfg.delay_seconds == 5
    assert cfg.max_wait_seconds == 30
    assert cfg.safety_minutes == 120
    assert cfg.backups_keep == 1
    assert cfg.backups_min_interval_minutes == 1


def test_max_wait_never_below_delay():
    cfg = core.Config(delay_seconds=500, max_wait_seconds=60).clamped()
    assert cfg.max_wait_seconds >= cfg.delay_seconds


def test_load_config_handles_strings_from_settings():
    values = {"enabled": "false", "delay_seconds": "45", "pause_while_editing": "true",
              "backups_keep": "abc"}
    cfg = core.load_config(lambda key, default: values.get(key, default))
    assert cfg.enabled is False
    assert cfg.delay_seconds == 45
    assert cfg.pause_while_editing is True
    assert cfg.backups_keep == core.Config().backups_keep


def test_save_then_load_roundtrip():
    store = {}
    cfg = core.Config(enabled=False, delay_seconds=33, show_message_bar=True)
    core.save_config(store.__setitem__, cfg)
    loaded = core.load_config(lambda k, d: store.get(k, d))
    assert loaded == cfg.clamped()


def test_backup_create_list_prune(tmp_path):
    project = tmp_path / "zone.qgz"
    project.write_bytes(b"abc")
    stamps = [datetime(2026, 10, 16, 10, 0, s) for s in range(5)]
    created = [core.create_backup(str(project), now=t) for t in stamps]
    assert all(os.path.isfile(p) for p in created)
    assert core.backup_dir_for(str(project)).endswith("zone_sauvegardes")
    assert core.list_backups(str(project)) == sorted(created)

    removed = core.prune_backups(str(project), keep=3)
    assert len(removed) == 2
    assert core.list_backups(str(project)) == sorted(created)[2:]


def test_backup_same_second_does_not_overwrite(tmp_path):
    project = tmp_path / "zone.qgz"
    project.write_bytes(b"abc")
    now = datetime(2026, 10, 16, 10, 0, 0)
    first = core.create_backup(str(project), now=now)
    second = core.create_backup(str(project), now=now)
    assert first != second
    assert len(core.list_backups(str(project))) == 2


def test_backup_ignores_missing_or_empty_files(tmp_path):
    assert core.create_backup(str(tmp_path / "absent.qgz")) is None
    empty = tmp_path / "vide.qgz"
    empty.write_bytes(b"")
    assert core.create_backup(str(empty)) is None


def test_list_backups_ignores_other_projects(tmp_path):
    a = tmp_path / "a.qgz"
    a.write_bytes(b"1")
    core.create_backup(str(a), now=datetime(2026, 1, 1, 0, 0, 0))
    folder = core.backup_dir_for(str(a))
    open(os.path.join(folder, "autre_20260101_000000.qgz"), "wb").close()
    open(os.path.join(folder, "notes.txt"), "wb").close()
    assert len(core.list_backups(str(a))) == 1


def test_backup_scheduler_first_always_then_interval():
    s = core.BackupScheduler()
    assert s.due("p", 0, 600) is True
    s.mark("p", 0)
    assert s.due("p", 599, 600) is False
    assert s.due("p", 600, 600) is True
    assert s.due("autre", 1, 600) is True


def test_file_is_writable(tmp_path):
    f = tmp_path / "x.qgz"
    f.write_bytes(b"1")
    assert core.file_is_writable(str(f)) is True
    assert core.file_is_writable(str(tmp_path / "absent")) is None
    assert core.file_is_writable("") is None


# --------------------------------------------------------------------------
# Version 0.2 : délais adaptatifs, indicateur, vérification, restauration
# --------------------------------------------------------------------------
import zipfile  # noqa: E402


def test_effective_delays_default_without_measure():
    cfg = core.Config()
    assert core.effective_delays(cfg) == (20, 120)
    assert core.effective_delays(cfg, 0.4) == (20, 120)  # enregistrement rapide


def test_effective_delays_grow_with_slow_saves():
    cfg = core.Config()
    assert core.effective_delays(cfg, 3.0) == (30, 120)
    # 20 s d'enregistrement : pause de 200 s, le délai maximal suit
    assert core.effective_delays(cfg, 20.0) == (200, 200)


def test_effective_delays_capped_and_switchable():
    assert core.effective_delays(core.Config(), 500.0)[0] == core.MAX_DELAY_SECONDS
    off = core.Config(adaptive_delay=False)
    assert core.effective_delays(off, 20.0) == (20, 120)


def test_indicator_texts():
    assert core.indicator_text(core.IND_IDLE) == "AutoSave : actif"
    assert core.indicator_text(core.IND_OFF) == "AutoSave : désactivé"
    assert "en attente" in core.indicator_text(core.IND_PENDING)
    assert core.indicator_text(core.IND_SAVED, stamp="14:32") == "AutoSave : enregistré à 14:32"
    assert "traitement" in core.indicator_text(core.IND_PAUSED_TASK)
    assert "échec" in core.indicator_text(core.IND_ERROR)
    assert core.indicator_text(core.IND_PAUSED_EDIT, "routes") == \
        "AutoSave : en pause (édition de routes)"
    long_name = core.indicator_text(core.IND_PAUSED_EDIT, "a" * 60)
    assert long_name.endswith("…)") and len(long_name) < 60
    assert core.indicator_text(core.IND_PAUSED_EDIT) == "AutoSave : en pause (édition)"


def _qgz(path, name="p.qgs", content="<qgis></qgis>"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, content)


def test_verify_valid_qgz(tmp_path):
    f = tmp_path / "a.qgz"
    _qgz(f)
    assert core.verify_project_file(str(f)) == (True, "")


def test_verify_rejects_broken_files(tmp_path):
    missing = tmp_path / "absent.qgz"
    assert core.verify_project_file(str(missing))[0] is False

    empty = tmp_path / "vide.qgz"
    empty.write_bytes(b"")
    assert core.verify_project_file(str(empty)) == (False, "fichier vide")

    garbage = tmp_path / "x.qgz"
    garbage.write_bytes(b"PK\x03\x04 tronque")
    assert core.verify_project_file(str(garbage))[0] is False

    no_project = tmp_path / "y.qgz"
    _qgz(no_project, name="notes.txt", content="hello")
    assert core.verify_project_file(str(no_project)) == (False, "projet absent de l'archive")


def test_verify_truncated_zip_is_rejected(tmp_path):
    good = tmp_path / "g.qgz"
    _qgz(good, content="<qgis>" + "x" * 5000 + "</qgis>")
    cut = tmp_path / "c.qgz"
    cut.write_bytes(good.read_bytes()[:-40])
    assert core.verify_project_file(str(cut))[0] is False


def test_verify_qgs_head_and_tail(tmp_path):
    ok = tmp_path / "a.qgs"
    ok.write_text('<?xml version="1.0"?>\n<!DOCTYPE qgis>\n<qgis version="3.40"></qgis>\n')
    assert core.verify_project_file(str(ok)) == (True, "")
    truncated = tmp_path / "b.qgs"
    truncated.write_text('<?xml version="1.0"?>\n<qgis version="3.40"><layer-tree>')
    assert core.verify_project_file(str(truncated)) == (False, "fin du projet absente")
    nohead = tmp_path / "c.qgs"
    nohead.write_text("n'importe quoi</qgis>")
    assert core.verify_project_file(str(nohead)) == (False, "en-tête du projet absent")


def test_verify_unknown_extension_is_not_checked(tmp_path):
    f = tmp_path / "x.dat"
    f.write_bytes(b"abc")
    assert core.verify_project_file(str(f)) == (True, "")


def test_snapshot_and_restore_previous(tmp_path):
    f = tmp_path / "zone.qgz"
    _qgz(f, content="<qgis>v1</qgis>")
    snap = core.snapshot_previous(str(f))
    assert snap == core.previous_path(str(f))
    f.write_bytes(b"corrompu")
    assert core.restore_previous(str(f)) is True
    assert core.verify_project_file(str(f)) == (True, "")


def test_snapshot_skips_unhealthy_file_and_keeps_previous_good_copy(tmp_path):
    f = tmp_path / "zone.qgz"
    _qgz(f, content="<qgis>bon</qgis>")
    core.snapshot_previous(str(f))
    f.write_bytes(b"abime")
    assert core.snapshot_previous(str(f)) is None
    assert core.restore_previous(str(f)) is True  # le bon cliché est resté


def test_restore_previous_without_snapshot(tmp_path):
    f = tmp_path / "zone.qgz"
    _qgz(f)
    assert core.restore_previous(str(f)) is False


def test_previous_snapshot_is_not_listed_as_backup(tmp_path):
    f = tmp_path / "zone.qgz"
    _qgz(f)
    core.snapshot_previous(str(f))
    assert core.list_backups(str(f)) == []


def test_describe_backups_newest_first_with_sizes(tmp_path):
    f = tmp_path / "zone.qgz"
    f.write_bytes(b"x" * 2048)
    core.create_backup(str(f), now=datetime(2026, 10, 16, 9, 0, 0))
    core.create_backup(str(f), now=datetime(2026, 10, 16, 11, 30, 5))
    infos = core.describe_backups(str(f))
    assert [i.when.hour for i in infos] == [11, 9]
    assert infos[0].size == 2048


def test_human_size():
    assert core.human_size(842) == "842 o"
    assert core.human_size(12.4 * 1024) == "12,4 Ko"
    assert core.human_size(3.1 * 1024 * 1024) == "3,1 Mo"


def test_restore_copy_never_overwrites(tmp_path):
    project = tmp_path / "zone.qgz"
    project.write_bytes(b"actuel")
    backup = core.create_backup(str(project), now=datetime(2026, 10, 16, 9, 0, 0))
    now = datetime(2026, 10, 16, 12, 0, 0)
    first = core.restore_copy(backup, str(project), now=now)
    second = core.restore_copy(backup, str(project), now=now)
    assert os.path.basename(first) == "zone_restaure_20261016_120000.qgz"
    assert first != second and os.path.isfile(second)
    assert project.read_bytes() == b"actuel"


# --- Modification externe du fichier projet ---------------------------------
def test_file_signature_changes_with_content_and_is_none_when_absent(tmp_path):
    target = tmp_path / "a.qgz"
    assert core.file_signature(str(target)) is None
    assert core.file_signature("") is None
    target.write_bytes(b"abc")
    first = core.file_signature(str(target))
    target.write_bytes(b"abcdef")
    assert core.file_signature(str(target)) != first


def test_changed_externally_needs_a_known_signature_and_an_existing_file(tmp_path):
    target = tmp_path / "a.qgz"
    target.write_bytes(b"abc")
    known = core.file_signature(str(target))
    assert core.changed_externally(None, str(target)) is False
    assert core.changed_externally(known, str(target)) is False
    target.write_bytes(b"autre contenu")
    assert core.changed_externally(known, str(target)) is True
    target.unlink()
    assert core.changed_externally(known, str(target)) is False


def test_external_change_blocks_automatic_saving_only():
    assert ev(external_change=True).reason == core.R_EXTERNAL
    assert ev(external_change=True).action == core.SKIP
    assert ev(external_change=True, manual=True).action == core.SAVE
    # Les autres règles gardent la priorité utile : rien à enregistrer reste « clean »
    assert ev(external_change=True, is_dirty=False).reason == core.R_CLEAN


def test_conflict_indicator_text():
    assert "modifié ailleurs" in core.indicator_text(core.IND_CONFLICT)


def test_read_version_from_metadata(tmp_path):
    meta = tmp_path / "metadata.txt"
    meta.write_text("[general]\nname=AutoSave Data\nversion = 1.2.3\nauthor=X\n", encoding="utf-8")
    assert core.read_version(str(meta)) == "1.2.3"
    assert core.read_version(str(tmp_path / "absent.txt")) == ""
    meta.write_text("[general]\nname=X\n", encoding="utf-8")
    assert core.read_version(str(meta)) == ""


# --- Âge lisible, sessions interrompues -----------------------------------------
def test_relative_age_labels():
    now = datetime(2026, 10, 17, 12, 0, 0)
    assert core.relative_age(datetime(2026, 10, 17, 11, 59, 40), now) == "à l'instant"
    assert core.relative_age(datetime(2026, 10, 17, 11, 57, 0), now) == "il y a 3 min"
    assert core.relative_age(datetime(2026, 10, 17, 9, 30, 0), now) == "il y a 2 h"
    assert core.relative_age(datetime(2026, 10, 15, 11, 0, 0), now) == "il y a 2 j"
    assert core.relative_age(datetime(2026, 10, 17, 12, 5, 0), now) == "à l'instant"  # horloge en avance


def test_pid_alive_knows_itself_and_rejects_nonsense():
    assert core.pid_alive(os.getpid()) is True
    assert core.pid_alive(0) is False and core.pid_alive(-5) is False
    assert core.pid_alive("abc") is False and core.pid_alive(None) is False


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _tracker(tmp_path, pid, clock):
    return core.SessionTracker(str(tmp_path), pid=pid, clock=clock)


def test_clean_exit_leaves_nothing_to_report(tmp_path):
    clock = Clock()
    session = _tracker(tmp_path, 111, clock)
    session.start("/p/a.qgz")
    assert os.path.isfile(session.path)
    session.mark_clean()
    assert not os.path.exists(session.path)
    assert _tracker(tmp_path, 222, clock).find_stale(alive=lambda pid: False) == []


def test_dead_session_is_reported_with_its_project_and_last_save(tmp_path):
    clock = Clock()
    crashed = _tracker(tmp_path, 111, clock)
    crashed.start("/p/a.qgz")
    clock.t += 60
    crashed.set_last_save()
    clock.t += 5000                       # le PC redémarre plus tard
    fresh = _tracker(tmp_path, 222, clock)
    stale = fresh.find_stale(alive=lambda pid: False)
    assert len(stale) == 1
    assert stale[0]["project"] == "/p/a.qgz" and stale[0]["last_save"] == 1_000_060.0
    fresh.discard(stale[0])
    assert fresh.find_stale(alive=lambda pid: False) == []


def test_running_session_with_fresh_heartbeat_is_not_reported(tmp_path):
    clock = Clock()
    other = _tracker(tmp_path, 111, clock)
    other.start("/p/a.qgz")
    clock.t += 20
    me = _tracker(tmp_path, 222, clock)
    assert me.find_stale(alive=lambda pid: True) == []


def test_live_pid_with_old_heartbeat_counts_as_dead(tmp_path):
    """Numéro de processus réutilisé après un redémarrage : seul le signe de vie tranche."""
    clock = Clock()
    old = _tracker(tmp_path, 111, clock)
    old.start("/p/a.qgz")
    clock.t += core.STALE_AFTER_SECONDS + 1
    me = _tracker(tmp_path, 222, clock)
    assert len(me.find_stale(alive=lambda pid: True)) == 1


def test_same_pid_from_a_previous_boot_is_stale(tmp_path):
    clock = Clock()
    old = _tracker(tmp_path, 111, clock)
    old.start("/p/a.qgz")
    clock.t += 10                          # démarrage rapide, même numéro de processus
    me = _tracker(tmp_path, 111, clock)
    assert len(me.find_stale(alive=lambda pid: True)) == 1


def test_unreadable_journal_is_dropped_silently(tmp_path):
    clock = Clock()
    me = _tracker(tmp_path, 222, clock)
    os.makedirs(me.dir)
    broken = os.path.join(me.dir, "1_1.json")
    with open(broken, "w") as handle:
        handle.write("{pas du json")
    assert me.find_stale(alive=lambda pid: False) == []
    assert not os.path.exists(broken)


def test_journal_write_failure_never_raises(tmp_path):
    blocker = tmp_path / "AutoSaveData"
    blocker.write_text("un fichier là où il faudrait un dossier")
    session = _tracker(tmp_path, 111, Clock())
    session.start("/p/a.qgz")               # ne doit pas lever d'exception
    session.heartbeat()
