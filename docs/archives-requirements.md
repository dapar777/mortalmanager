# Archivy jako v Total Commanderu – požadavky

Stav před implementací: `src/archive/` má `ArchiveHandler` (zip/tar/7z) + `ArchiveManager`,
ale **GUI je nepoužívá**. Panel má jen dvě vlastní metody s `zipfile` napřímo
(`_extract_here`, `_compress_to_zip`), obě běží **synchronně na GUI vlákně** a jen pro ZIP.
`JobType.EXTRACT` / `COMPRESS` existují, ale `JobQueue._execute` je neumí.

Cíl: archiv se chová jako složka. Procházení, rozbalení, zabalení, přidání souboru,
editace souboru uvnitř, mazání – vše přes frontu jobů, nic blokujícího na GUI vlákně.

## 1. Formáty a knihovny

| Req | Popis | Priorita | Stav |
|---|---|---|---|
| F1 | **ZIP** čtení i zápis (stdlib `zipfile`) – plná podpora včetně zápisu | must | hotovo |
| F2 | **TAR** a komprimované varianty `.tar.gz/.tgz/.tar.bz2/.tbz2/.tar.xz/.txz` (stdlib `tarfile`) | must | hotovo |
| F3 | Holé `.gz/.bz2/.xz` (jeden soubor) – čtení a rozbalení | should | hotovo |
| F4 | **7z** přes `py7zr`, pokud je nainstalované; bez něj se formát tváří jako nepodporovaný (žádný pád) | should | hotovo |
| F5 | **RAR** jen ke čtení a rozbalení (`rarfile` + unrar), zápis nikdy; bez knihovny se nenabízí | could | hotovo |
| F6 | Detekce formátu **podle obsahu (magic bytes), ne jen podle přípony** – přejmenovaný archiv se pozná | must | hotovo |
| F7 | Formát, který umí jen číst, musí GUI hlásit předem (položky „přidat/smazat“ neaktivní, ne chyba až při akci) | must | hotovo |

## 2. Procházení archivu jako složky

| Req | Popis | Priorita | Stav |
|---|---|---|---|
| B1 | Enter na archivu **vstoupí dovnitř** místo spuštění asociací; Ctrl+PgDn stejně jako v TC | must | hotovo |
| B2 | Cesta v panelu je `C:\dir\soubor.zip\podslozka`; breadcrumb i pole cesty ji ukazují a dají se z ní vrátit | must | hotovo |
| B3 | Uvnitř archivu funguje `..`, Backspace a tlačítko nahoru; z kořene archivu vedou zpět do složky s archivem, **kurzor na archivu** | must | hotovo |
| B4 | Výpis má virtuální stromovou strukturu: položky archivu se skládají do složek, i když archiv ukládá jen ploché cesty | must | hotovo |
| B5 | Sloupce Size/Date/Attr mají smysluplné hodnoty (nekomprimovaná velikost, čas z archivu); ratio není nutné | should | hotovo |
| B6 | Výpis archivu běží **v `QThreadPool` workeru** jako normální složka (generace, aby pomalý výpis nepřepsal novější) | must | hotovo |
| B7 | Rychlý filtr (Ctrl+S), řazení, označování, Tab mezi panely fungují uvnitř archivu beze změny | must | hotovo |
| B8 | Výpis velkého archivu se **cachuje** podle (cesta, mtime, velikost), aby každá změna adresáře nečetla archiv znovu | should | hotovo |
| B9 | Vnořený archiv uvnitř archivu: Enter nabídne rozbalení do temp a otevření (ne nekonečné vnoření) | could | neimplementováno (vnořený archiv otevře prohlížeč souboru) |

## 3. Rozbalení (extract)

| Req | Popis | Priorita | Stav |
|---|---|---|---|
| X1 | **F5 z archivu do druhého panelu** = rozbalit označené (nebo vše, není-li nic označeno) | must | hotovo |
| X2 | Kontextové menu „Extract Here“ (do složky s archivem) a „Extract to <jméno>\“ (do podsložky) | must | hotovo |
| X3 | Rozbalení **zachová strukturu podsložek** a relativní cesty vůči aktuální pozici v archivu | must | hotovo |
| X4 | Běží jako **job** (`JobType.EXTRACT`) s průběhem, možností zrušit, Toastem na konci | must | hotovo |
| X5 | **Ochrana proti path traversal** (`..`, absolutní cesty, na Windows i `C:`): položka mimo cíl se přeskočí a zaloguje | must | hotovo |
| X6 | Konflikt s existujícím souborem řeší stejný dialog jako kopírování (přepsat / přeskočit / vše) | should | hotovo |
| X7 | Chyba u jedné položky nezruší celý job; sesbírané chyby se ukážou na konci | must | hotovo |

## 4. Zabalení (compress)

| Req | Popis | Priorita | Stav |
|---|---|---|---|
| C1 | Dialog „Pack files“ (Alt+F5): cílová cesta+jméno, formát, **ukládat cesty**, přesunout do archivu, úroveň komprese | must | hotovo |
| C2 | Výchozí jméno: u jedné položky její jméno, u více jméno aktuální složky; výchozí cíl = **druhý panel** jako v TC | must | hotovo |
| C3 | Zabalení složky vezme **celý strom** rekurzivně, relativně ke společnému kořeni | must | hotovo |
| C4 | Běží jako **job** (`JobType.COMPRESS`) s průběhem a rušením | must | hotovo |
| C5 | „Přesunout do archivu“ smaže zdroje **až po úspěšném dokončení** | should | hotovo |
| C6 | Existující cílový archiv: zeptat se přepsat / přidat do něj / zrušit | should | hotovo |

## 4b. Hesla (zašifrované archivy)

| Req | Popis | Priorita | Stav |
|---|---|---|---|
| P1 | Vstup do šifrovaného archivu se **zeptá na heslo** dialogem, ne chybou | must | hotovo |
| P2 | Špatné heslo se pozná a ptá se znovu (3 pokusy), zrušení navigaci zruší | must | hotovo |
| P3 | Heslo se pamatuje po dobu běhu programu, **nikdy se neukládá na disk** | must | hotovo |
| P4 | Rozbalení, čtení (F3), editace (F4), přidání i mazání použijí zapamatované heslo | must | hotovo |
| P5 | Zabalení s heslem (AES-256) u formátů, které to umí (7z); heslo se zadává dvakrát | must | hotovo |
| P6 | Úprava šifrovaného archivu ho **nechá šifrovaný** (přebalí se se stejným heslem) | must | hotovo |
| P7 | Formát, který šifrovat neumí, volbu hesla nenabídne; šifrovaný ZIP se nepřepisuje (stdlib neumí zapsat ZipCrypto) | must | hotovo |
| P8 | Hesla se zapomenou při zavření okna | should | hotovo |

**Podpora podle formátů:** číst šifrované umí ZIP (ZipCrypto), 7z a RAR; **vytvořit** šifrovaný umí jen 7z.
TAR a holé gz/bz2/xz šifrování nemají vůbec.

## 5. Úpravy archivu na místě

| Req | Popis | Priorita | Stav |
|---|---|---|---|
| E1 | **Přidání souborů** do otevřeného archivu: F5/Ctrl+V/drop z druhého panelu přidá do aktuální podsložky archivu | must | hotovo |
| E2 | **Mazání** položek v archivu (F8/Delete) u zapisovatelných formátů | must | hotovo |
| E3 | **F3 prohlížeč** na souboru v archivu: rozbalí do temp a otevře (jen ke čtení) | must | hotovo |
| E4 | **F4 editor**: rozbalí do temp, otevře editor a po uložení **zapíše zpátky do archivu** (TC se ptá) | must | hotovo |
| E5 | Zápis do ZIP jde přes **bezpečné přepsání**: nový soubor vedle a atomický `os.replace`, aby pád nezničil archiv | must | hotovo |
| E6 | Temp soubory jdou do vlastní složky a uklidí se při zavření okna | should | hotovo |
| E7 | Po každé změně archivu se **zneplatní cache** a panel se obnoví | must | hotovo |

## 6. Integrace do zbytku aplikace

| Req | Popis | Priorita | Stav |
|---|---|---|---|
| I1 | **Každá funkce má položku v paletě příkazů** (`_build_palette_commands`) – pravidlo projektu | must | hotovo |
| I2 | Klávesy jako v TC: Enter/Ctrl+PgDn vstup, Alt+F5 zabalit, Alt+F9 rozbalit, F5 rozbalit/přidat | must | hotovo |
| I3 | Kontextové menu v panelu: Extract Here / Extract to… / Pack…; v archivu Extract / Delete | must | hotovo |
| I4 | F-lišta a stavový řádek uvnitř archivu ukazují smysluplný stav (počet položek, „(archive)“) | should | hotovo |
| I5 | Operace, které v archivu nedávají smysl (terminál zde, shell menu, VCS, drag ven do Exploreru) se **neuplatní nebo jsou skryté** | must | hotovo |
| I6 | Indexer archivy neindexuje (zůstává na souborech na disku) | must | hotovo |
| I7 | Vzhled beze změny: ikony z `icons`, žádný hex mimo `theme.py`, velikosti přes `theme.px()` | must | hotovo |

## 7. Robustnost a testy

| Req | Popis | Priorita | Stav |
|---|---|---|---|
| T1 | Jednotkové testy `tests/test_archive.py`: list/extract/create/add/delete pro zip i tar, path traversal, detekce podle obsahu | must | hotovo |
| T2 | Testy virtuálního stromu (ploché cesty → složky, vnořená cesta, `..`) | must | hotovo |
| T3 | Poškozený / neúplný archiv = chybová hláška, ne pád aplikace | must | hotovo |
| T4 | Archiv chráněný heslem: ZIP i 7z se **zeptají na heslo** místo pádu (min. čitelná chyba) | should | hotovo |
| T5 | Nic blokujícího na GUI vlákně – všechny operace přes job frontu nebo `QThreadPool` | must | hotovo |
| T6 | Unicode jména a cesty s diakritikou (ZIP bez UTF-8 flagu = cp437 fallback) | should | hotovo |

## Stav

Hotovo vše kromě jedné položky nejnižší priority:

- **B9** (vnořený archiv v archivu) – Enter na archivu uvnitř archivu ho otevře jako soubor
  (rozbalí do temp a pošle prohlížeči), do vnořeného archivu se nevstupuje.

Testy: `tests/test_archive.py` (vrstva), `test_archive_jobs.py` (joby), `test_archive_panel.py`
(procházení v panelu), `test_archive_actions.py` (příkazy okna a dialog),
`test_archive_password.py` (hesla).

## Pořadí prací

1. **Vrstva `archive/`**: rozšířit handlery (F1–F7), přidat `delete_members`, `write_member`,
   virtuální strom, detekci podle obsahu, bezpečné přepsání. Testy T1–T3, T6.
2. **Joby**: `JobQueue._execute` pro EXTRACT a COMPRESS (X4, C4), průběh a rušení.
3. **Panel jako archivní prohlížeč**: `ArchiveLocation`, výpis v workeru, navigace, `..` (B1–B8).
4. **Operace z GUI**: extract (X1–X3, X6), pack dialog (C1–C3, C5–C6), add/delete (E1, E2).
5. **F3/F4 nad položkou v archivu** (E3–E5), temp správa (E6).
6. **Integrace**: paleta, klávesy, kontextové menu, F-lišta, zákazy (I1–I7).
7. Průběžně CLAUDE.md a testy.
