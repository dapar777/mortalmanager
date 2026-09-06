"""Terminal placeholders %N %P %T %S %R %SI %RI %% (core.cmdline)."""

from __future__ import annotations

from src.core.cmdline import CmdContext, expand, has_placeholders, quote

CTX = CmdContext(
    cursor_name="a.txt",
    panel_path="C:\\proj",
    other_path="D:\\out dir",
    selected_names=["a.txt", "my file.txt"],
    selected_paths=["C:\\proj\\a.txt", "C:\\proj\\my file.txt"],
)


def test_quote():
    assert quote("a.txt") == "a.txt"
    assert quote("my file.txt") == '"my file.txt"'
    assert quote('say "hi"') == '"say ""hi"""'
    assert quote("") == '""'


def test_has_placeholders():
    assert has_placeholders("copy %N %T")
    assert has_placeholders("echo 100%%")
    assert not has_placeholders("echo 100%")       # a lone % is not a placeholder
    assert not has_placeholders("dir")


def test_single_expansion():
    assert expand("copy %N %T", CTX) == ['copy a.txt "D:\\out dir"']
    assert expand("echo %P", CTX) == ["echo C:\\proj"]
    assert expand("zip out.zip %S", CTX) == ['zip out.zip a.txt "my file.txt"']
    assert expand("tool %R", CTX) == ['tool C:\\proj\\a.txt "C:\\proj\\my file.txt"']
    assert expand("echo 100%% done", CTX) == ["echo 100% done"]
    assert expand("dir", CTX) == ["dir"]


def test_iterative_expansion():
    assert expand("gzip %SI", CTX) == ["gzip a.txt", 'gzip "my file.txt"']
    assert expand("code %RI", CTX) == ["code C:\\proj\\a.txt", 'code "C:\\proj\\my file.txt"']
    # both iterate over the same entry; %S inside stays the whole list
    assert expand("echo %SI of %S", CTX) == ['echo a.txt of a.txt "my file.txt"',
                                             'echo "my file.txt" of a.txt "my file.txt"']
    empty = CmdContext(panel_path="C:\\x")
    assert expand("gzip %SI", empty) == ['gzip ""']
    assert expand("echo %N", empty) == ['echo ""']


def test_repl_mode_raw_and_strict():
    assert expand("print('%SI')", CTX, quoted=False) == ["print('a.txt')", "print('my file.txt')"]
    assert expand("open(r'%RI')", CTX, quoted=False)[1] == "open(r'C:\\proj\\my file.txt')"
    assert has_placeholders("'%d%%' % x", strict=True) is False
    assert has_placeholders("'%d%%' % x") is True
    assert has_placeholders("print('%N')", strict=True) is True
