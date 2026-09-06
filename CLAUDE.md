# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Co to je

MortalManager — dvoupanelový souborový manažer pro Windows ve stylu Total Commanderu (Python 3.12+, PySide6).
Rozdělaný projekt: jádro (panely, taby, kopírování/přesun/mazání přes frontu jobů, archivy, FTP/SFTP, prohlížeč F3,
editor F4, vestavěný terminál, command palette, VCS badge u souborů) je funkční, ale bez README a bez balení.
`pyproject.toml` odkazuje na `README.md`, který neexistuje. Kód, docstringy a komentáře jsou anglicky.

## Příkazy

Aplikace se spouští jako modul z kořene repa (`src/main.py` si sám přidá kořen do `sys.path`). Na stroji autora
je venv v `C:\mm_venv` (Python 3.12, PySide6, pytest); `restart.bat` zabije běžící instanci a spustí novou z tohoto venvu.
`pyproject.toml` původně vyžadoval Python 3.13 a měl neexistující build backend; opraveno na `>=3.12` a
`setuptools.build_meta` (ruff/mypy cílí dál na 3.13).

```bash
python -m src.main                                  # spustit aplikaci
restart.bat                                         # restart běžící instance (C:\mm_venv)
python -m pytest -q                                 # testy (59, běží ~2 s, bez GUI)
python -m pytest -q tests/test_core.py              # jeden soubor
python -m pytest -q tests/test_core.py::test_format_size_kb   # jeden test
python -m ruff check src tests                      # lint (ruff a mypy nejsou v C:\mm_venv nainstalované)
python -m mypy src                                  # typy, strict
pip install -e .[dev]                               # vývojová instalace
```

Testy pokrývají jen `core`, `filesystem`, `archive` a `database` (čisté funkce, dočasné adresáře, in-memory
SQLite); GUI testy neexistují, `qt_api` v pytest konfiguraci hlásí varování, protože `pytest-qt` není nainstalován.
Kořenové `temp_shell*_debug*.py` jsou jednorázové průzkumné skripty z ladění nativní Windows shell context menu
(pywin32 `SHParseDisplayName`, `IContextMenu`); samotná funkce je v `PanelWidget._show_context_menu`.

## Architektura

Vrstvy jsou balíčky pod `src/`, GUI závisí na všech ostatních, ostatní na GUI ne:

- **`core/`** — čisté doménové typy bez Qt: `FileEntry`, `DriveInfo`, `SortField`, `SearchQuery`,
  `OperationProgress` (`file_model.py`), `SelectionManager`, `NavigationHistory` (zpět/vpřed),
  `UndoManager` (zásobník vratných souborových operací).
- **`filesystem/`** — `FileSystemProvider` (ABC) s async metodami; `LocalFileSystemProvider` je Windows-optimalizovaný
  (atributy přes `win32`, koš, typy disků, hledání, výpočet velikosti), `DirectoryWatcher` emituje Qt signál při změně
  adresáře. FTP/SFTP (`ftp/`) jsou samostatní klienti, ne implementace providera.
- **`jobs/`** — `JobQueue` (QObject) běží nad **asyncio smyčkou**, kterou `src/main.py` pumpuje z Qt `QTimer`
  každých 20 ms (`loop.call_soon(loop.stop); loop.run_forever()`). Dlouhé operace (copy/move/delete/search)
  se odesílají jako `JobSpec` z `MainWindow`, průběh chodí signály do `CopyDialog`; joby umí pause/resume/cancel.
  Cokoli blokujícího musí jít touto cestou, ne přímo z GUI vlákna.
- **`archive/`** — `ArchiveHandler` (ABC) + zip/tar/7z handlery za `ArchiveManager`; archivy se procházejí jako
  virtuální adresáře.
- **`database/`** — `DatabaseManager` nad SQLite v `%APPDATA%\MortalManager` (nastavení, záložky, oblíbené, FTP
  relace, historie příkazů a cest, historie operací, uložené taby). **`settings/ConfigManager`** je singleton
  (`get_instance()`) nad touž DB s dataclassami `AppConfig`/`PanelConfig`.
- **`plugins/`** — `PluginManager` načítá moduly z plugin adresáře; typy `ListerPlugin` (náhled), `ContentPlugin`
  (metadata), `PackerPlugin` (archivy).
- **`gui/`** — `MainWindow` (menu, toolbar, F-klávesová lišta, zkratky, command palette, příkazová řádka s historií
  a doplňováním, přepínání témat, relaunch jako admin) drží dva `PanelWidget` (levý/pravý, aktivní strana se
  přepíná Tabem). `PanelWidget` = adresní řádek + taby + `FileTableView`/`FileTableModel` + lišta disků; načítá
  adresář asynchronně, anotuje git/svn stav položek a hlídá změny watcherem. Dialogy jsou v `gui/dialogs/`,
  vestavěný terminál v `terminal_widget.py`. Téma (dark/light) je QSS generované v `src/main.py`
  (`apply_theme`), lze přepnout za běhu.
- **`viewer/`, `editor/`** — samostatná okna pro F3/F4.

Konvence: Qt widgety komunikují signály, ne přímými voláními do rodiče; stav, který má přežít restart, patří do
`DatabaseManager`, ne do souborů; `FileEntry.full_path` je jediný zdroj cesty položky.
