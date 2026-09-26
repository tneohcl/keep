"""Portable Qt construction/clipboard check; no Borg or user-state writes."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
with tempfile.TemporaryDirectory() as directory:
    os.environ["KEEP_CONFIG_PATH"] = str(Path(directory) / "config.json")
    os.environ["XDG_STATE_HOME"] = str(Path(directory) / "state")
    import main
    main.LOGDIR = str(Path(directory) / "logs")
    Path(main.LOGDIR).mkdir()
    for day, verdict in (("14", "with warnings"), ("15", "successfully")):
        (Path(main.LOGDIR) / f"backup-202609{day}-093000.log").write_text(
            f"2026-09-{day}T09:30:00+08:00 Keep backup started\nbackup completed {verdict}\n")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtGui import QPalette, QColor, QFontDatabase, QFont
    app = QApplication([])
    if os.name == "nt":
        font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
        assert font_id >= 0
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
    # Health logs must never leak success from another repository or an old ID.
    scope_logs = Path(directory) / "scoped-logs"
    scope_logs.mkdir()
    for name, repo, repo_id in (("01", "/a", "a-id"), ("02", "/b", "b-id")):
        (scope_logs / f"backup-{name}.log").write_text(
            f"2026-09-25T08:00:00+08:00 Repository: {repo}\n"
            f"2026-09-25T08:00:00+08:00 Repository ID: {repo_id}\nbackup completed successfully\n")
    with patch.object(main, "LOGDIR", str(scope_logs)):
        assert main.last_backup_attempt_status("/a", "a-id")[0] == "ok"
        assert main.last_backup_attempt_status("/a", "replacement-id")[0] == "never run"
        # Unknown ID: this location's history, marked unverified - never "no backups".
        assert main.backup_history("/a", None) == ("ok", "2026-09-25T08:00:00+08:00", False)
        assert main.backup_history("/a", "a-id")[2] is True
        assert main.backup_history("/c", None)[0] == "never run"
    # Large results remain within the screen, with all diagnostics accessible.
    from keep_ui.restore_results import RestoreResultsDialog
    results = RestoreResultsDialog(None, [f"App {i}: /restored/app-{i}" for i in range(300)],
                                   ["Failed app: " + "long/path/" * 400], "/restored")
    results.show()
    app.processEvents()
    assert results.height() <= results.screen().availableGeometry().height()
    assert results.details.verticalScrollBar().maximum() > 0
    assert "App 299" in results.details.toPlainText()
    assert "Failed app:" in results.details.toPlainText()
    results.grab().save("ui-review/restore-results.png")
    results.close()
    results.deleteLater()
    # Bulk actions check catalog entries, including Folders, without selecting hidden apps.
    from keep_ui.restore_picker import RestorePicker
    picker = RestorePicker("", installed_apps_provider=lambda: type("Installed", (), {"contains": lambda self, *ids: "Krita" in ids})())
    picker.populate([main.CatalogEntry("Krita", None, [("config/krita", "/config/krita")], "krita", "applications"),
                     main.CatalogEntry("Old app", None, [("config/old", "/config/old")], "old", "applications"),
                     main.CatalogEntry("Documents", None, [("docs", "/docs")], "folder", "folders")])
    picker.select_all_button.click()
    assert len(picker._all_items()) == 2
    picker.set_list_view()
    assert len(picker._all_items()) == 2
    picker.clear_selection_button.click()
    assert not picker._all_items()
    picker.reset_to_placeholder()
    assert not picker.select_all_button.isEnabled()
    picker.deleteLater()
    # Known native data joins the same app's Flatpak tile without losing paths.
    archive_home = Path(directory) / "archive" / main.HOME_IN_ARCHIVE
    for relative in (".var/app/org.gimp.GIMP", ".config/GIMP", ".var/app/org.kde.krita", ".local/share/krita"):
        folder = archive_home / relative
        folder.mkdir(parents=True)
        (folder / "settings").write_text("data")
    with patch.object(main, "MOUNTPOINT", str(Path(directory) / "archive")), patch.object(main, "CROSS_INSTALL_APPS", {}), patch.object(main, "CURATED_ITEMS", []), patch.object(main, "EXTRA_PATHS", {}), patch.object(main, "flatpak_app_names", return_value={"org.gimp.GIMP": "GNU Image Manipulation Program", "org.kde.krita": "Krita"}):
        catalog = main.build_app_catalog()
        for appid in ("org.gimp.GIMP", "org.kde.krita"):
            entries = [entry for entry in catalog if entry.icon == appid]
            assert len(entries) == 1, entries
            assert len(entries[0].rel_paths) == 2, entries

    from keep_ui.checkable_list import CheckableListWidget
    from PySide6.QtWidgets import QListWidgetItem, QStyleOptionViewItem, QStyle
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import Qt, QPointF, QEvent
    choices = CheckableListWidget()
    entry = QListWidgetItem("A long application name with several words")
    entry.setFlags(entry.flags() | Qt.ItemIsUserCheckable)
    entry.setCheckState(Qt.Unchecked)
    choices.addItem(entry)
    choices.resize(400, 180)
    choices.show()
    app.processEvents()
    for mode in (main.QListWidget.IconMode, main.QListWidget.ListMode):
        choices.setViewMode(mode)
        app.processEvents()
        option = QStyleOptionViewItem()
        option.initFrom(choices)
        choices.itemDelegate().initStyleOption(option, choices.model().index(0, 0))
        option.rect = choices.visualItemRect(entry)
        checkbox = choices.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator, option, choices).center()
        for point in (option.rect.center(), checkbox):
            for expected in (Qt.Checked, Qt.Unchecked):
                for event_type, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton), (QEvent.MouseButtonRelease, Qt.NoButton)):
                    event = QMouseEvent(event_type, QPointF(point), QPointF(choices.viewport().mapToGlobal(point)), Qt.LeftButton, buttons, Qt.NoModifier)
                    app.sendEvent(choices.viewport(), event)
                assert entry.checkState() == expected
    choices.close()
    choices.deleteLater()

    main.CONFIG["setup_complete"] = True
    main.CONFIG["destination"]["label"] = "External backup drive"
    with patch.object(main.MainWindow, "refresh_status", lambda self: None):
        window = main.MainWindow()
    assert app.styleSheet() and "$" not in app.styleSheet()
    assert window.btn_backup.property("role") == "primary"
    # Exercise queued worker-to-GUI delivery across the 32-bit boundary.
    from PySide6.QtCore import QObject, QThread, Slot
    totals = [2**31 - 1, 2**31, 21404408200, 2**40, 2**63 - 1]
    class ProgressProbe(QObject):
        def __init__(self):
            super().__init__()
            self.received = []
        @Slot("qint64")
        def receive(self, value):
            assert QThread.currentThread() == app.thread()
            self.received.append(value)
    class LargeBackupProbe(main.BackupWorker):
        def run(self):
            for value in totals:
                self.prescan_total.emit(value)
    progress_probe = ProgressProbe()
    progress_worker = LargeBackupProbe()
    progress_worker.prescan_total.connect(progress_probe.receive)
    progress_worker.prescan_total.connect(window._on_prescan_total)
    progress_worker.start()
    assert progress_worker.wait(5000)
    app.processEvents()
    assert progress_probe.received == totals
    assert window._prescan_total_bytes == totals[-1]
    progress_worker.deleteLater()
    window._prescan_total_bytes = None
    window.backup_progress.hide()
    window.log_view.setPlainText("Keep backup started\nRepository: /private/path\nbackup completed with warnings")
    window.copy_summary_button.click()
    assert app.clipboard().text() == "Keep backup started\nbackup completed with warnings"
    window.lbl_headline.setText("Last backup completed successfully")
    window.lbl_last.setText("Today, 09:30")
    window.lbl_next.setText("Tomorrow, 04:00")
    window.lbl_dest_status.setText("External backup drive")
    window.show()
    app.processEvents()
    assert not window.copy_summary_button.isVisible()
    window.action_show_log.trigger()
    app.processEvents()
    assert window.copy_summary_button.isVisible()
    assert window.action_show_log.isChecked()
    window.log_section.toggle.click()
    assert not window.action_show_log.isChecked()
    assert window.action_back_up_now.shortcut().toString() == "Ctrl+B"
    assert window.archive_combo.accessibleName()
    assert window.pages.currentIndex() == 0
    with patch.object(main.MainWindow, "ensure_mounted", lambda self: (_ for _ in ()).throw(AssertionError("Navigation must not mount"))):
        window.pages.setCurrentIndex(1)
        assert window.pages.currentIndex() == 1
        window.action_show_log.setChecked(True)
        assert window.pages.currentIndex() == 0
        window.action_show_log.setChecked(False)
    window._set_repo_actions_enabled(False)
    assert not window.btn_compare_archives.isEnabled()
    window._set_repo_actions_enabled(True)
    from mount_service import MountState
    window.mount_coordinator.state = MountState.OPENING
    with patch.object(window, "_ensure_mounted_impl") as nested_mount:
        assert window.ensure_mounted() is False
        nested_mount.assert_not_called()
    window.mount_coordinator.state = MountState.IDLE
    with patch.object(window, "_ensure_mounted_impl", side_effect=RuntimeError("fixture")):
        try:
            window.ensure_mounted()
        except RuntimeError:
            pass
        assert not window._opening_archive
    from types import SimpleNamespace
    with patch.object(main.subprocess, "run", return_value=SimpleNamespace(stdout="borg 1.4", returncode=0)):
        about = main.AboutDialog(window)
    about.show()
    app.processEvents()
    compact_size = about.size()
    for _ in range(3):
        about.sysinfo_section.toggle.click()
        app.processEvents()
        assert about.height() > compact_size.height()
        about.sysinfo_section.toggle.click()
        app.processEvents()
        app.processEvents()
        assert about.size() == compact_size, (about.size(), compact_size)
    about.close()
    about.deleteLater()
    main.REPO = str(Path(directory) / "fixture-repo")
    with patch.object(main.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr="")):
        assert window._attempt_mount("fixture") == (True, "")
        window._browse_operation_lock.release()
    for failure in (main.subprocess.TimeoutExpired("borg", 60), OSError("fixture unavailable")):
        with patch.object(main.subprocess, "run", side_effect=failure):
            ok, message = window._attempt_mount("fixture")
            assert not ok and message
    assert isinstance(window.pages, main.QStackedWidget)
    assert window.pages.count() == 2 and window.view_switch.currentIndex() == 0
    window.view_switch.setCurrentIndex(1)
    assert window.pages.currentIndex() == 1
    assert window.btn_review_restore.property("role") == "primary" and window.btn_backup.property("role") != "primary"
    assert not window.btn_review_restore.isHidden() and window.btn_restore.isHidden()
    # Review restore always defaults to a new Keep-Restored folder and says so.
    from keep_ui.review_restore import ReviewRestoreDialog, display_path
    review = ReviewRestoreDialog([f"item {n}" for n in range(12)], "Today at 8:51 AM", main.new_restore_folder(), main.HOME, window)
    assert "Keep-Restored" in review.destination and review.destination_label.text().replace("\\", "/").startswith("~/Keep-Restored/")
    assert review.restore_button.isDefault() and review.restore_button.property("role") == "primary"
    assert display_path("/elsewhere/x", "/home/me") == "/elsewhere/x"
    review.deleteLater()
    # Choosing a parent folder must preserve existing content and choose a child.
    chosen_parent = Path(directory) / "existing-output"
    chosen_parent.mkdir()
    with patch("keep_ui.review_restore.QFileDialog.getExistingDirectory", return_value=str(chosen_parent)):
        review._change_folder()
    assert Path(review.destination).parent == chosen_parent and not Path(review.destination).exists()
    # Copying yields to the GUI event loop and finishes before releasing the dialog.
    from keep_ui.restore_worker import run_restore
    from keep_ui import safe_copy
    from PySide6.QtCore import QTimer
    import time
    source = Path(directory) / "copy-source"
    source.write_text("restore bytes")
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    original_copy = safe_copy.copy_new
    def slow_copy(*args):
        time.sleep(.05)
        return original_copy(*args)
    with patch.object(safe_copy, "copy_new", side_effect=slow_copy):
        done, failed, skipped = run_restore(window, [(source, "file", "File")], str(Path(directory) / "copy-output"))
    timer.stop()
    assert done and not failed and ticks, "The UI must process events while restoring"
    window.view_switch.setCurrentIndex(0)
    assert window.btn_backup.property("role") == "primary" and window.btn_review_restore.isHidden()
    # Three separate facts; an untested recovery says so and offers the test.
    assert list(window.status_facts.facts) == ["backup", "check", "recovery"]
    assert window.status_facts.whenText("recovery") == window.status_facts.NEVER
    assert window.btn_test_recovery.text() == "Test recovery…"
    assert window.action_test_recovery.text() == "Test recovery…"
    from keep_ui.recovery_test_dialog import RecoveryTestDialog
    dialog = RecoveryTestDialog("/nonexistent/repo", window)
    assert dialog.passphrase.echoMode() == main.QLineEdit.Password
    assert not dialog.start_button.isEnabled()
    dialog.passphrase.setText("typed")
    assert dialog.start_button.isEnabled() and dialog.start_button.property("role") == "primary"
    dialog.deleteLater()
    # Restore sidebar: stored backups by friendly name drive the (hidden) archive model.
    # (mounting is patched throughout: these archives exist only in this test.)
    with patch.object(window, "ensure_mounted", return_value=True), patch.object(window, "_load_visible_archive", lambda *a, **k: None):
        window._apply_archive_listing({"archives": [
            {"name": "keep-host-2026-09-24_040000", "time": "2026-09-24T04:00:00.000000"},
            {"name": "keep-host-2026-09-25_085149", "time": "2026-09-25T08:51:49.000000"}]}, select_latest=True)
        panel = window.backup_list_panel
        assert panel.list.count() == 2 and window.archive_combo.isHidden()
        assert "newest" in panel.list.item(0).data(main.Qt.UserRole + 1)
        assert "keep-host" not in panel.list.item(0).text()
        panel.list.setCurrentRow(1)
        assert window.archive_combo.currentText() == "keep-host-2026-09-24_040000"
        assert window.friendly_archive("keep-host-2026-09-24_040000") in window.archive_friendly.text()
        window.archive_combo.setCurrentIndex(0)
        assert panel.list.currentRow() == 0
        window.view_switch.setCurrentIndex(1)
        assert window.sidebar_stack.currentWidget() is window.backup_list_panel
        window.view_switch.setCurrentIndex(0)
        assert window.sidebar_stack.currentWidget() is window.status_sidebar and window.status_sidebar.widget() is window.backup_panel
        # Search hides tiles (and empty sections); a click anywhere on a tile toggles it once.
        picker = window.apps_picker
        picker._installed_apps_provider = None  # don't depend on what this machine has installed
        picker.populate([main.CatalogEntry("Firefox", None, [("f", "/f")], "firefox", "applications"),
                     main.CatalogEntry("Krita", None, [("k", "/k")], "krita", "applications")])
        section = picker._sections["applications"]
        picker.search.setText("fire")
        assert [section.item(i).isHidden() for i in range(section.count())] == [False, True]
        picker.search.setText("")
        picker.select_all_button.click()
        picker.search.setText("fire")
        assert "1 hidden" in picker.selection_summary.text()
        assert not picker.select_all_button.isEnabled()
        picker.clear_selection_button.click()
        assert not picker._all_items(), "Clear must also clear filtered-out selections"
        picker.search.setText("")
        from PySide6.QtCore import QPoint, QPointF, QEvent
        from PySide6.QtGui import QMouseEvent

        def click(widget, pos):  # QtTest isn't in Debian's PySide6 packages
            for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                event = QMouseEvent(kind, QPointF(pos), QPointF(widget.mapToGlobal(pos)),
                                    main.Qt.LeftButton, main.Qt.LeftButton if kind == QEvent.MouseButtonPress else main.Qt.NoButton,
                                    main.Qt.NoModifier)
                QApplication.sendEvent(widget, event)
        window.resize(1200, 800)
        window.show()
        window.view_switch.setCurrentIndex(1)
        app.processEvents()
        rect = section.visualItemRect(section.item(0))
        click(section.viewport(), rect.center())
        assert section.item(0).checkState() == main.Qt.Checked
        click(section.viewport(), rect.topLeft() + QPoint(18, rect.height() // 2))
        assert section.item(0).checkState() == main.Qt.Unchecked  # the drawn checkbox: exactly one toggle
        section.item(0).setCheckState(main.Qt.Checked)
        original_snapshot = tuple(section.item(0).data(main.Qt.UserRole))
        window._touch_activity()
        def review_and_reset(dialog):
            assert not window.unmount_timer.isActive()
            picker.reset_to_placeholder()
            return main.QDialog.Accepted
        with patch.object(main.ReviewRestoreDialog, "exec", review_and_reset), patch.object(picker, "restore_checked_safe") as restore:
            window.review_restore()
            restore.assert_called_once()
            assert restore.call_args.kwargs["entries"][0][1:] == original_snapshot[0]
        assert window.unmount_timer.isActive()
        window.view_switch.setCurrentIndex(0)
        window.hide()
    # Recovery access: verified facts apart from dated personal confirmations.
    from keep_ui import recovery_access
    import recovery_test
    window._refresh_facts({"repo": "/fixture/repo"})
    assert window.recovery_access_row.value() == "Not yet tested"
    access = recovery_access.RecoveryAccessDialog(
        "/fixture/repo", {"available": True, "label": "TITAN-i", "type": "network"},
        {"encryption": {"mode": "repokey-blake2"}, "repository": {"id": "fixture-id"}}, 2, "Today at 9:00 AM", main.borg_env, lambda: None, window)
    assert access.export_button.isEnabled() and access.test_button.property("role") == "primary"
    assert "sign in to TITAN-i" in access.confirm_boxes["destination_access"].text()
    access.confirm_boxes["passphrase_saved"].setChecked(True)
    assert recovery_test.confirmation(recovery_test.load_access("/fixture/repo", repository_id="fixture-id"), "passphrase_saved")[0] == "confirmed"
    access.confirm_boxes["passphrase_saved"].setChecked(False)
    assert recovery_test.load_access("/fixture/repo", repository_id="fixture-id") == {}
    assert recovery_access.encryption_fact({"encryption": {"mode": "keyfile-blake2"}})[0] == "warning"
    assert recovery_access.encryption_fact(None)[0] == "never"
    access.deleteLater()
    picker = window.apps_picker
    picker._installed_apps_provider = lambda: SimpleNamespace(contains=lambda *ids: "Firefox" in ids)
    picker.populate([main.CatalogEntry("Firefox", None, [("data", "/data")], "firefox", "applications")])
    section = next(iter(picker._sections.values()))
    assert section.selectionMode() == main.QAbstractItemView.NoSelection
    assert not section.dragEnabled()
    # App cells must retain checkbox/icon/text geometry on hover and selection.
    from PySide6.QtWidgets import QStyleOptionViewItem, QStyle
    from PySide6.QtCore import QRect
    for icon_mode in (True, False):
        picker.set_icon_view() if icon_mode else picker.set_list_view()
        geometries = []
        for extra_state in (QStyle.State_None, QStyle.State_MouseOver, QStyle.State_Selected,
                            QStyle.State_Selected | QStyle.State_MouseOver):
            option = QStyleOptionViewItem()
            option.initFrom(section)
            section.itemDelegate().initStyleOption(option, section.model().index(0, 0))
            option.rect = QRect(0, 0, section.gridSize().width(), section.gridSize().height())
            option.state = QStyle.State_Enabled | extra_state
            geometries.append(tuple(section.style().subElementRect(element, option, section)
                                    for element in (QStyle.SE_ItemViewItemCheckIndicator,
                                                    QStyle.SE_ItemViewItemDecoration,
                                                    QStyle.SE_ItemViewItemText)))
        assert all(geometry == geometries[0] for geometry in geometries), geometries

    section.item(0).setCheckState(main.Qt.Checked)
    assert len(picker._all_items()) == 1
    assert "1" in picker.selection_summary.text()
    picker.set_list_view()
    assert len(picker._all_items()) == 1
    section.item(0).setCheckState(main.Qt.Unchecked)
    assert not picker._all_items()
    picker.populate([
        main.CatalogEntry("Firefox", None, [("data", "/data")], "firefox", "applications"),
        main.CatalogEntry("Removed app", None, [("old", "/old")], "removed", "applications"),
    ])
    assert sum(section.count() for section in picker._sections.values()) == 1
    picker.show_uninstalled.setChecked(True)
    assert sum(section.count() for section in picker._sections.values()) == 2
    picker.show_uninstalled.setChecked(False)
    assert sum(section.count() for section in picker._sections.values()) == 1
    picker.reset_to_placeholder()
    with patch.object(window, "ensure_mounted", return_value=True) as mount:
        window.archive_combo.blockSignals(True)
        window.archive_combo.addItem("fixture")
        window.archive_combo.blockSignals(False)
        window.pages.setCurrentIndex(1)
        app.processEvents()
        mount.assert_called_once()
        window.pages.setCurrentIndex(0)
        window.archive_combo.blockSignals(True)
        window.archive_combo.clear()
        window.archive_combo.blockSignals(False)
    output = Path(__file__).parent / "ui-review"
    output.mkdir(exist_ok=True)
    for dark in (False, True):
        palette = app.style().standardPalette()
        if dark:
            for role, color in ((QPalette.Window, "#252525"), (QPalette.WindowText, "#eeeeee"), (QPalette.Base, "#1b1b1b"), (QPalette.Text, "#eeeeee"), (QPalette.Button, "#333333"), (QPalette.ButtonText, "#eeeeee"), (QPalette.Highlight, "#387cb4"), (QPalette.HighlightedText, "#ffffff")):
                palette.setColor(role, QColor(color))
        app.setPalette(palette)
        window.resize(1100, 720)
        app.processEvents()
        assert window.grab().save(str(output / ("dark.png" if dark else "light.png")))
        window.pages.setCurrentIndex(1)
        window.resize(1000, 700)
        app.processEvents()
        assert window.grab().save(str(output / ("restore-dark.png" if dark else "restore-light.png")))
        window.pages.setCurrentIndex(0)
    font = app.font()
    font.setPointSize(14)
    app.setFont(font)
    window.hide()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    with patch.object(main.MainWindow, "refresh_status", lambda self: None):
        window = main.MainWindow()
    window.lbl_headline.setText("Last backup completed successfully")
    window.lbl_last.setText("Today, 09:30")
    window.lbl_next.setText("Tomorrow, 04:00")
    window.lbl_dest_status.setText("External backup drive")
    window.resize(1000, 800)
    window.show()
    app.processEvents()
    assert window.grab().save(str(output / "large-text.png"))
    import applications
    app_home = Path(directory) / "app-home"
    for relative in (".mozilla", ".var/app/org.mozilla.firefox", ".config/kritarc", ".ssh", ".gnupg", ".local/share/kwalletd"):
        (app_home / relative).mkdir(parents=True)
    with patch.object(applications, "InstalledApps", return_value=SimpleNamespace(entry_installed=lambda entry: entry["id"] == "firefox", icon=lambda *names: None)):
        dialog = main.ApplicationSelectionDialog(main.CONFIG, app_home, window)
    dialog.all_data.setChecked(False)
    assert dialog.items.count() == 2
    assert not dialog.items.item(0).isHidden()
    assert dialog.items.item(1).isHidden()
    old_apps = next(box for box in dialog.findChildren(main.QCheckBox) if box.text() == "Show data for uninstalled apps")
    old_apps.setChecked(True)
    assert not dialog.items.item(1).isHidden()
    old_apps.setChecked(False)
    assert dialog.items.item(1).isHidden()
    assert all(dialog.items.item(i).data(main.Qt.UserRole)["category"] == "applications" for i in range(dialog.items.count()))
    dialog.items.item(1).setCheckState(main.Qt.Unchecked)
    assert dialog.selection() == ["firefox"]
    dialog.clear_selection_button.click()
    assert dialog.selection() == []
    dialog.all_data.setChecked(True)
    dialog.select_all_button.click()
    assert not dialog.all_data.isChecked()
    assert dialog.selection() == ["firefox"]
    # Tiles: checkbox inside the tile next to icon + name (CheckTileDelegate).
    from keep_ui.checkable_list import CheckTileDelegate
    assert isinstance(dialog.items.itemDelegate(), CheckTileDelegate) and dialog.items.itemDelegate().framed
    assert dialog.items.gridSize().width() == 210
    assert not dialog.items.item(0).icon().isNull()
    dialog.view_mode.setCurrentIndex(1)
    assert not dialog.items.itemDelegate().framed
    assert dialog.items.isWrapping()
    assert dialog.items.gridSize().width() == 280
    assert dialog.selection() == ["firefox"]
    dialog.view_mode.setCurrentIndex(0)
    dialog.items.setCurrentRow(0)
    assert ".mozilla" in dialog.path_details.toPlainText()
    dialog.show()
    app.processEvents()
    assert dialog.grab().save(str(output / "application-selection.png"))
    dialog.hide()
    dialog.deleteLater()
    with patch.object(applications.Path, "home", return_value=app_home):
        settings_dialog = main.ApplicationSelectionDialog(main.CONFIG, app_home, window, "system")
    assert settings_dialog.items.count() == 3
    assert all(settings_dialog.items.item(i).data(main.Qt.UserRole)["category"] == "system" for i in range(settings_dialog.items.count()))
    settings_dialog.deleteLater()
    window.hide()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    print("Qt construction, clipboard, log disclosure, shortcuts and rendered light/dark/large-text checks passed")
