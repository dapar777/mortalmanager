"""Archive commands of the main window: pack, extract, add, delete, view / edit.

A mixin rather than free functions: every command needs the active panel, the
job queue and the toasts, which all live on MainWindow. Nothing here touches an
archive directly – the work goes through the job queue (requirement T5).
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from src.archive import archive_manager as am
from src.jobs.job import JobSpec, JobType
from src.solarqt.widgets import Toast

logger = logging.getLogger(__name__)


class ArchiveActionsMixin:
    """Archive commands; mixed into MainWindow."""

    # ------------------------------------------------------------------ pack (C1-C6)

    def _pack_files(self) -> None:
        """Alt+F5: pack the selected items of the active panel."""
        from .dialogs.pack_dialog import PackDialog

        panel = self._active_panel_widget
        if panel.in_archive:
            Toast.show_message(self, "Packing works on files, not inside an archive", "warning")
            return
        paths = [e.full_path for e in panel.selected_entries() if not e.is_parent]
        if not paths:
            Toast.show_message(self, "Nothing to pack", "info")
            return

        other = self._inactive_panel_widget
        default_dir = other.current_path if not other.in_archive else panel.current_path
        dlg = PackDialog(paths, default_dir, self)
        if dlg.exec() != PackDialog.DialogCode.Accepted:
            return
        opts = dlg.options()
        if am.handler_for_new(opts.target) is None:
            Toast.show_message(self, f"Unknown archive format: {opts.target.name}", "error")
            return

        if opts.target.exists():
            choice = QMessageBox.question(
                self, "Pack Files",
                f"{opts.target.name} already exists.\n\n"
                "Yes – add the files to it\nNo – overwrite it",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return
            opts.append = choice == QMessageBox.StandardButton.Yes
            if not opts.append:
                try:
                    opts.target.unlink()
                except OSError as exc:
                    Toast.show_message(self, f"Cannot overwrite: {exc}", "error")
                    return

        spec = JobSpec(
            job_type=JobType.COMPRESS,
            sources=paths,
            destination=str(opts.target),
            options={
                "base_dir": panel.current_path,
                "level": opts.level,
                "store_paths": opts.store_paths,
                "append": opts.append,
                "move_sources": opts.move_to_archive,   # honoured in _on_job_finished (C5)
                "password": opts.password or (am.known_password(opts.target)
                                              if opts.append else None),
            },
            description=f"Pack {len(paths)} item(s) into {opts.target.name}",
        )
        self._submit(spec, f"Packing {opts.target.name}…")

    def _enter_archive(self) -> None:
        """Ctrl+PgDn: step into the archive under the cursor (B1)."""
        panel = self._active_panel_widget
        entry = panel.current_entry()
        if entry is None or entry.is_parent:
            return
        if entry.is_dir:
            panel.navigate_to(entry.full_path)
            return
        if not panel.enter_archive(entry.full_path):
            Toast.show_message(self, f"{entry.name} is not a supported archive", "info")

    # ------------------------------------------------------------------ extract (X1-X3)

    def _extract_to_other_panel(self) -> None:
        """F5 inside an archive: unpack the selection into the other panel."""
        panel = self._active_panel_widget
        if not panel.in_archive:
            Toast.show_message(self, "Not inside an archive", "info")
            return
        other = self._inactive_panel_widget
        if other.in_archive:
            Toast.show_message(self, "The other panel is inside an archive", "warning")
            return
        self._extract(panel, Path(other.current_path))

    def _extract_here(self, archive_path: str | None = None) -> None:
        """Unpack an archive into the folder it sits in (X2)."""
        panel = self._active_panel_widget
        if panel.in_archive:
            self._extract(panel, Path(panel.archive_location.archive).parent)
            return
        archive = Path(archive_path or self._selected_archive(panel) or "")
        if not archive.name:
            return
        self._submit_extract(archive, archive.parent, None, "")

    def _extract_to_subfolder(self, archive_path: str | None = None) -> None:
        """Unpack into a new folder named after the archive (X2)."""
        panel = self._active_panel_widget
        archive = Path(archive_path or self._selected_archive(panel) or "")
        if not archive.name:
            return
        target = archive.parent / _archive_stem(archive)
        self._submit_extract(archive, target, None, "")

    def _extract_to(self) -> None:
        """Alt+F9: ask where to unpack."""
        panel = self._active_panel_widget
        if panel.in_archive:
            start = str(Path(panel.archive_location.archive).parent)
        else:
            archive = self._selected_archive(panel)
            if archive is None:
                Toast.show_message(self, "Select an archive first", "info")
                return
            start = str(Path(archive).parent)
        chosen = QFileDialog.getExistingDirectory(self, "Extract to", start)
        if not chosen:
            return
        if panel.in_archive:
            self._extract(panel, Path(chosen))
        else:
            self._submit_extract(Path(self._selected_archive(panel)), Path(chosen), None, "")

    def _extract(self, panel, destination: Path) -> None:
        """Extract the panel's selection (or everything) out of the open archive."""
        location = panel.archive_location
        if location is None:
            return
        selected = [e for e in panel.selected_entries() if not e.is_parent]
        members = [location.child(e.name).inner for e in selected] or None
        self._submit_extract(location.archive, destination, members, location.inner)

    def _submit_extract(
        self,
        archive: Path,
        destination: Path,
        members: list[str] | None,
        strip_prefix: str,
    ) -> None:
        if am.get_handler(archive) is None:
            Toast.show_message(self, f"{archive.name} is not a supported archive", "error")
            return
        if not self._active_panel_widget.ensure_password(str(archive)):
            return                                   # encrypted and the user cancelled
        overwrite = True
        if destination.exists() and any(destination.iterdir()):
            # X6: one question for the whole job, like the copy dialog's checkbox
            choice = QMessageBox.question(
                self, "Extract",
                f"{destination.name} is not empty.\n\n"
                "Yes – overwrite existing files\n"
                "No – keep them and unpack the rest",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return
            overwrite = choice == QMessageBox.StandardButton.Yes
        what = f"{len(members)} item(s)" if members else "all files"
        spec = JobSpec(
            job_type=JobType.EXTRACT,
            sources=[str(archive)],
            destination=str(destination),
            options={"archive": str(archive), "members": members,
                     "strip_prefix": strip_prefix, "overwrite": overwrite,
                     "password": am.known_password(archive)},
            description=f"Extract {what} from {archive.name}",
        )
        self._submit(spec, f"Extracting {archive.name}…")

    # ------------------------------------------------------------------ edit (E1, E2)

    def _archive_add(self, paths: list[str], panel=None) -> None:
        """Copy files from disk into the open archive (F5 / Ctrl+V / drop)."""
        panel = panel or self._active_panel_widget
        location = panel.archive_location
        if location is None or not paths:
            return
        if not am.is_writable(location.archive):
            Toast.show_message(
                self, f"{am.format_name(location.archive)} archives are read-only", "warning")
            return
        base = str(Path(paths[0]).parent)
        spec = JobSpec(
            job_type=JobType.ARCHIVE_ADD,
            sources=paths,
            destination=str(location),
            options={"archive": str(location.archive), "base_dir": base,
                     "prefix": location.inner,
                     "password": am.known_password(location.archive)},
            description=f"Add {len(paths)} item(s) to {location.archive.name}",
        )
        self._submit(spec, f"Adding to {location.archive.name}…")

    def _archive_delete(self) -> None:
        """F8 / Delete inside an archive."""
        panel = self._active_panel_widget
        location = panel.archive_location
        if location is None:
            return
        if not am.is_writable(location.archive):
            Toast.show_message(
                self, f"{am.format_name(location.archive)} archives are read-only", "warning")
            return
        entries = [e for e in panel.selected_entries() if not e.is_parent]
        if not entries:
            return
        names = ", ".join(e.name for e in entries[:3])
        if len(entries) > 3:
            names += f" and {len(entries) - 3} more"
        if QMessageBox.question(
            self, "Delete from Archive",
            f"Delete {names} from {location.archive.name}?",
        ) != QMessageBox.StandardButton.Yes:
            return
        members = [location.child(e.name).inner for e in entries]
        spec = JobSpec(
            job_type=JobType.ARCHIVE_DELETE,
            sources=members,
            destination=str(location),
            options={"archive": str(location.archive),
                     "password": am.known_password(location.archive)},
            description=f"Delete {len(members)} item(s) from {location.archive.name}",
        )
        self._submit(spec, f"Deleting from {location.archive.name}…")

    # ------------------------------------------------------------------ view / edit (E3-E5)

    def _open_archive_member(self, entry, edit: bool = False) -> bool:
        """F3 / F4 / Enter on a file inside an archive: unpack it to a temp file and
        open it. On F4 the file is written back when it changed (E4)."""
        panel = self._active_panel_widget
        location = panel.archive_location
        if location is None or entry.is_dir:
            return False
        temp = panel.temp_extracts()
        member = location.child(entry.name).inner
        try:
            local = temp.extract(location.archive, member)
        except Exception as exc:
            Toast.show_message(self, f"Cannot open {entry.name}: {exc}", "error")
            return True
        if edit:
            self._edit_archive_member(panel, local, entry.name)
        else:
            from src.viewer.file_viewer import FileViewerWindow
            FileViewerWindow(str(local), self).show()
        return True

    def _edit_archive_member(self, panel, local: Path, name: str) -> None:
        """Open the temp copy in the editor; the built-in one is modal, so the
        write-back happens right after it closes (E4)."""
        from src.editor.file_editor import FileEditorWindow

        FileEditorWindow([str(local)], self).exec()
        if am.is_writable(panel.archive_location.archive):
            self._save_archive_member(panel, local, name)

    def _save_archive_member(self, panel, local: Path, name: str) -> None:
        """Write an edited temp file back into its archive, asking first (E4)."""
        temp = panel._temp
        if temp is None or not temp.changed(local):
            return
        if QMessageBox.question(
            self, "Save to Archive", f"Write the changed {name} back into the archive?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            temp.save_back(local)
        except Exception as exc:
            Toast.show_message(self, f"Cannot update the archive: {exc}", "error")
            return
        Toast.show_message(self, f"{name} updated in the archive", "success")
        panel.refresh()

    # ------------------------------------------------------------------ helpers

    def _selected_archive(self, panel) -> str | None:
        """Path of the archive under the cursor, or None with a hint to the user."""
        entry = panel.current_entry()
        if entry is None or entry.is_dir:
            Toast.show_message(self, "Select an archive first", "info")
            return None
        if am.get_handler(Path(entry.full_path)) is None:
            Toast.show_message(self, f"{entry.name} is not a supported archive", "warning")
            return None
        return entry.full_path


def _archive_stem(archive: Path) -> str:
    """"src.tar.gz" -> "src" (a multi-part extension goes as a whole)."""
    name = archive.name
    lowered = name.lower()
    for ext in sorted(am.ARCHIVE_EXTENSIONS, key=len, reverse=True):
        if lowered.endswith("." + ext):
            return name[: -(len(ext) + 1)]
    return archive.stem
