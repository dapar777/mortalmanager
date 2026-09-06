# MortalManager

Dvoupanelový souborový manažer pro Windows ve stylu Total Commanderu (Python 3.12+, PySide6).

**Stav:** rozdělaný projekt. Jádro je funkční: dva panely s taby, kopírování/přesun/mazání přes frontu jobů
s průběhem a pause/resume/cancel, archivy (zip, tar, 7z), FTP/SFTP, prohlížeč (F3) a editor (F4), vestavěný
terminál, command palette, hledání, hromadné přejmenování, duplicity, hashe, oblíbené, nativní shell context menu,
git/svn stav souborů, tmavé a světlé téma. Chybí balení, dokumentace a GUI testy.

## Spuštění

```bash
pip install -e .[dev]
python -m src.main
```

## Testy a lint

```bash
python -m pytest -q
python -m ruff check src tests
python -m mypy src
```

Nastavení a stav aplikace se ukládají do SQLite v `%APPDATA%\MortalManager`.
