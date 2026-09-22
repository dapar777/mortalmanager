"""Encrypted archives: reading with a password, creating one, the session cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.archive import archive_manager as am
from src.archive.base import ArchiveError, PasswordRequired, WrongPassword
from src.archive.sevenzip_handler import _HAS_PY7ZR, SevenZipHandler
from src.archive.tar_handler import TarHandler
from src.archive.zip_handler import ZipHandler

needs_py7zr = pytest.mark.skipif(not _HAS_PY7ZR, reason="py7zr is not installed")


@pytest.fixture(autouse=True)
def _clean_passwords():
    am.forget_passwords()
    yield
    am.forget_passwords()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    (root / "docs").mkdir(parents=True)
    (root / "readme.txt").write_text("hello", encoding="utf-8")
    (root / "docs" / "index.md").write_text("# index", encoding="utf-8")
    return root


@pytest.fixture
def encrypted(tmp_path: Path, tree: Path) -> Path:
    """A 7z archive protected with the password "pw"."""
    out = tmp_path / "secret.7z"
    am.create_archive(out, [tree], base_dir=tree.parent, password="pw")
    am.forget_passwords()                      # creating remembers it; start clean
    return out


# ------------------------------------------------------------------ capabilities

def test_format_capabilities():
    assert ZipHandler().supports_password and not ZipHandler().can_encrypt
    assert not TarHandler().supports_password and not TarHandler().can_encrypt
    if _HAS_PY7ZR:
        assert SevenZipHandler().supports_password and SevenZipHandler().can_encrypt


def test_plain_archive_needs_no_password(tmp_path: Path, tree: Path):
    plain = tmp_path / "plain.zip"
    am.create_archive(plain, [tree], base_dir=tree.parent)
    assert not am.needs_password(plain)
    assert am.known_password(plain) is None


@pytest.mark.parametrize("name", ["a.tar", "a.tar.gz", "a.zip"])
def test_formats_that_cannot_encrypt_refuse_a_password(tmp_path: Path, tree: Path, name: str):
    with pytest.raises(ArchiveError):
        am.create_archive(tmp_path / name, [tree], base_dir=tree.parent, password="pw")


# ------------------------------------------------------------------ 7z round trip

@needs_py7zr
def test_create_and_read_an_encrypted_archive(encrypted: Path, tmp_path: Path):
    assert am.needs_password(encrypted)
    assert am.can_encrypt(encrypted)

    assert am.read_member(encrypted, "src/readme.txt", password="pw") == b"hello"
    out = tmp_path / "out"
    am.extract_archive(encrypted, out, password="pw")
    assert (out / "src" / "docs" / "index.md").read_text(encoding="utf-8") == "# index"


@needs_py7zr
def test_wrong_password_is_reported_as_such(encrypted: Path):
    with pytest.raises(WrongPassword):
        am.read_member(encrypted, "src/readme.txt", password="nope")


@needs_py7zr
def test_missing_password_is_reported_as_such(encrypted: Path, tmp_path: Path):
    with pytest.raises(PasswordRequired):
        am.extract_archive(encrypted, tmp_path / "out")


@needs_py7zr
def test_verify_password(encrypted: Path):
    assert am.verify_password(encrypted, "pw")
    assert not am.verify_password(encrypted, "wrong")


@needs_py7zr
def test_editing_keeps_the_archive_encrypted(encrypted: Path, tmp_path: Path):
    am.write_member(encrypted, "src/readme.txt", b"edited", password="pw")
    assert am.needs_password(encrypted)                      # still protected
    assert am.read_member(encrypted, "src/readme.txt", password="pw") == b"edited"

    extra = tmp_path / "extra.txt"
    extra.write_text("more", encoding="utf-8")
    am.add_to_archive(encrypted, [extra], extra.parent, "src", password="pw")
    assert am.needs_password(encrypted)
    assert am.read_member(encrypted, "src/extra.txt", password="pw") == b"more"

    am.delete_from_archive(encrypted, ["src/docs"], password="pw")
    assert am.needs_password(encrypted)
    names = {e.path for e in am.list_archive(encrypted, password="pw")}
    assert "src/docs/index.md" not in names and "src/readme.txt" in names


# ------------------------------------------------------------------ session cache

@needs_py7zr
def test_remembered_password_is_used_automatically(encrypted: Path, tmp_path: Path):
    am.remember_password(encrypted, "pw")
    assert am.known_password(encrypted) == "pw"
    assert am.read_member(encrypted, "src/readme.txt") == b"hello"   # no password passed
    am.extract_archive(encrypted, tmp_path / "out")                  # nor here

    am.forget_passwords()
    assert am.known_password(encrypted) is None
    with pytest.raises(PasswordRequired):
        am.read_member(encrypted, "src/readme.txt")


def test_remember_and_forget_are_per_archive(tmp_path: Path):
    a, b = tmp_path / "a.7z", tmp_path / "b.7z"
    am.remember_password(a, "one")
    am.remember_password(b, "two")
    assert am.known_password(a) == "one" and am.known_password(b) == "two"
    am.remember_password(a, None)
    assert am.known_password(a) is None and am.known_password(b) == "two"


def test_password_lookup_is_case_insensitive_like_windows(tmp_path: Path):
    am.remember_password(tmp_path / "Secret.7z", "pw")
    assert am.known_password(tmp_path / "secret.7z") == "pw"


# ------------------------------------------------------------------ ZIP

def test_encrypted_zip_is_detected_and_refuses_rewriting(tmp_path: Path, tree: Path,
                                                         monkeypatch):
    """The standard library can read ZipCrypto but not write it, so an encrypted
    ZIP must be read-only rather than silently losing its encryption."""
    zip_path = tmp_path / "enc.zip"
    am.create_archive(zip_path, [tree / "readme.txt"])
    handler = ZipHandler()
    monkeypatch.setattr(ZipHandler, "needs_password", lambda self, p: True)

    assert am.needs_password(zip_path)
    with pytest.raises(ArchiveError):
        handler.delete_members(zip_path, ["readme.txt"])
    with pytest.raises(ArchiveError):
        handler.write_member(zip_path, "readme.txt", b"x")
    with pytest.raises(ArchiveError):
        handler.add_files(zip_path, [tree / "readme.txt"], tree)


def test_zip_password_errors_are_classified():
    handler = ZipHandler()
    path = Path("x.zip")
    assert isinstance(
        handler._as_password_error(RuntimeError("File x.txt is encrypted, password required"),
                                   path, given=False), PasswordRequired)
    assert isinstance(
        handler._as_password_error(RuntimeError("Bad password for file 'x.txt'"),
                                   path, given=True), WrongPassword)
    other = handler._as_password_error(RuntimeError("something else"), path, given=False)
    assert isinstance(other, ArchiveError) and not isinstance(other, PasswordRequired)


# ------------------------------------------------------------------ dialogs

def test_password_dialog_returns_what_was_typed(qapp):
    from src.solarqt import theme
    from src.gui.dialogs.password_dialog import PasswordDialog

    theme.apply(qapp, "light", zoom=1.0)
    dlg = PasswordDialog("secret.7z")
    try:
        dlg._edit.setText("pw")
        assert dlg.password == "pw" and dlg.remember
        from PySide6.QtWidgets import QLineEdit
        assert dlg._edit.echoMode() == QLineEdit.EchoMode.Password
        dlg._show.setChecked(True)
        assert dlg._edit.echoMode() == QLineEdit.EchoMode.Normal
    finally:
        dlg.deleteLater()


def test_new_password_dialog_requires_both_fields_to_match(qapp):
    from src.solarqt import theme
    from src.gui.dialogs.password_dialog import NewPasswordDialog

    theme.apply(qapp, "light", zoom=1.0)
    dlg = NewPasswordDialog()
    try:
        dlg._first.setText("pw")
        assert not dlg._ok.isEnabled()              # second field still empty
        dlg._again.setText("px")
        assert not dlg._ok.isEnabled() and "differ" in dlg._status.text()
        dlg._again.setText("pw")
        assert dlg._ok.isEnabled() and dlg.password == "pw"
    finally:
        dlg.deleteLater()


def test_pack_dialog_offers_encryption_only_for_7z(qapp, tree: Path, tmp_path: Path):
    from src.solarqt import theme
    from src.gui.dialogs.pack_dialog import PackDialog

    theme.apply(qapp, "light", zoom=1.0)
    dlg = PackDialog([str(tree)], str(tmp_path))
    try:
        assert not dlg._encrypt.isEnabled()          # ZIP by default
        assert dlg.options().password is None
        if _HAS_PY7ZR:
            dlg._format.setCurrentIndex([e for _l, e in dlg._formats].index("7z"))
            assert dlg._encrypt.isEnabled()
            dlg._password = "pw"                     # as if the user typed it
            dlg._encrypt.blockSignals(True)
            dlg._encrypt.setChecked(True)
            dlg._encrypt.blockSignals(False)
            assert dlg.options().password == "pw"
    finally:
        dlg.deleteLater()
