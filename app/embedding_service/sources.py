"""
Find and read importable paper metadata in a folder.

Supported inputs
----------------
* MySQL / MariaDB MyISAM tables: <table>.frm + <table>.MYD + <table>.MYI
  (for example a copy of XAMPP's mysql/data/<database> folder).  The files are
  staged into the MariaDB service and read back with SQL.
* RIS exports: *.ris
"""

import datetime
import html
import os
import re
import shutil
from dataclasses import dataclass, field

from config import (
    MARIADB_DATADIR,
    MARIADB_HOST,
    MARIADB_PASSWORD,
    MARIADB_PORT,
    MARIADB_USER,
)

from .errors import IngestError


# MariaDB on Linux only opens these exact spellings.
MYISAM_EXTENSIONS = {".frm": ".frm", ".myd": ".MYD", ".myi": ".MYI"}
RIS_EXTENSIONS = {".ris"}
UPLOAD_EXTENSIONS = set(MYISAM_EXTENSIONS) | RIS_EXTENSIONS

# Candidate column names (lower case) for each paper field, in priority order.
FIELD_COLUMNS = {
    "title": ("title", "article_title", "paper_title", "dc_title"),
    "abstract": ("abstract", "abstracts", "summary", "description"),
    "authors": ("author", "authors", "creator", "creators"),
    "keywords": ("keywords", "keyword", "author_keywords", "subjects"),
    "year": ("year", "pub_year", "publication_year", "pubyear"),
    "journal": ("journal", "journal_name", "journal_title", "source_title", "publication"),
    "doi": ("doi",),
}

# MyISAM "table is crashed / needs repair" error codes.
_CRASHED_ERRORS = {126, 127, 134, 144, 145, 1034, 1194, 1195}

_TAG_RE = re.compile(r"</?(?:jats:|mml:)?[A-Za-z]+(?:\s+[A-Za-z_:.-]+\s*=\s*\"[^\"]*\")*\s*/?>")
_SPACE_RE = re.compile(r"\s+")
_YEAR_RE = re.compile(r"\b(1[5-9]\d\d|2[01]\d\d)\b")


# =====================================================
# Folder scanning
# =====================================================

@dataclass
class TableFiles:
    name: str
    files: dict = field(default_factory=dict)  # lower-case extension -> path
    size: int = 0

    @property
    def missing(self):
        return [ext for ext in (".frm", ".myd") if ext not in self.files]

    @property
    def complete(self):
        return not self.missing

    def to_dict(self):
        return {
            "name": self.name,
            "size": self.size,
            "complete": self.complete,
            "missing": [MYISAM_EXTENSIONS[ext] for ext in self.missing],
            "needs_index_rebuild": self.complete and ".myi" not in self.files,
        }


@dataclass
class RisFile:
    name: str
    path: str
    size: int

    def to_dict(self):
        return {"name": self.name, "size": self.size}


@dataclass
class FolderScan:
    path: str
    tables: list
    ris_files: list
    warnings: list

    @property
    def importable(self):
        return any(t.complete for t in self.tables) or bool(self.ris_files)

    def to_dict(self):
        return {
            "tables": [t.to_dict() for t in self.tables],
            "ris_files": [r.to_dict() for r in self.ris_files],
            "warnings": self.warnings,
            "importable": self.importable,
            "total_size": sum(t.size for t in self.tables if t.complete)
            + sum(r.size for r in self.ris_files),
        }


def scan_folder(path: str) -> FolderScan:
    tables = {}
    ris_files = []
    innodb = []

    for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
        if not entry.is_file() or entry.name.startswith("."):
            continue

        stem, ext = os.path.splitext(entry.name)
        ext = ext.lower()

        if ext in MYISAM_EXTENSIONS:
            table = tables.setdefault(stem, TableFiles(stem))
            table.files[ext] = entry.path
            table.size += entry.stat().st_size
        elif ext in RIS_EXTENSIONS:
            ris_files.append(RisFile(entry.name, entry.path, entry.stat().st_size))
        elif ext == ".ibd":
            innodb.append(stem)

    warnings = []
    for table in tables.values():
        if not table.complete:
            if table.name in innodb:
                warnings.append(
                    f"{table.name}: InnoDB table (.ibd) – only MyISAM tables "
                    "(.frm/.MYD/.MYI) can be imported from files."
                )
            else:
                missing = ", ".join(MYISAM_EXTENSIONS[e] for e in table.missing)
                warnings.append(f"{table.name}: missing {missing}; table skipped.")

    return FolderScan(path, list(tables.values()), ris_files, warnings)


# =====================================================
# Normalisation
# =====================================================

def clean_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")

    text = html.unescape(str(value))
    text = _TAG_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def split_list(value):
    """Authors/keywords are stored as 'a;b;c' in the MySQL exports."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r";", clean_text(value))
    return [item for item in (clean_text(i) for i in items) if item]


def normalize_year(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime.date, datetime.datetime)):
        return str(value.year)
    if isinstance(value, int):
        return str(value) if value > 0 else ""

    match = _YEAR_RE.search(str(value))
    return match.group(1) if match else ""


def make_paper(source, title=None, abstract=None, authors=None, keywords=None,
               year=None, journal=None, doi=None):
    return {
        "title": clean_text(title),
        "abstract": clean_text(abstract),
        "authors": split_list(authors),
        "keywords": split_list(keywords),
        "year": normalize_year(year),
        "journal": clean_text(journal),
        "doi": clean_text(doi),
        "source": source,
    }


def map_columns(columns):
    """Map paper fields to the table's actual column names (case-insensitive)."""
    by_lower = {c.lower(): c for c in columns}
    mapping = {}
    for field_name, candidates in FIELD_COLUMNS.items():
        for candidate in candidates:
            if candidate in by_lower:
                mapping[field_name] = by_lower[candidate]
                break
    return mapping


# =====================================================
# RIS
# =====================================================

def iter_ris_papers(ris_file: RisFile):
    import rispy

    with open(ris_file.path, "r", encoding="utf-8", errors="ignore") as f:
        entries = rispy.load(f)

    for e in entries:
        yield make_paper(
            source=ris_file.name,
            title=e.get("title") or e.get("primary_title"),
            abstract=e.get("abstract"),
            authors=e.get("authors") or e.get("first_authors"),
            keywords=e.get("keywords"),
            year=e.get("year") or e.get("publication_year"),
            journal=e.get("journal_name") or e.get("secondary_title"),
            doi=e.get("doi"),
        )


# =====================================================
# MySQL / MariaDB (MyISAM files)
# =====================================================

def _quote(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def mariadb_connect(database=None, connect_timeout=10, **kwargs):
    import pymysql

    try:
        return pymysql.connect(
            host=MARIADB_HOST,
            port=MARIADB_PORT,
            user=MARIADB_USER,
            password=MARIADB_PASSWORD,
            database=database,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=connect_timeout,
            **kwargs,
        )
    except pymysql.err.OperationalError as exc:
        raise IngestError(
            f"Cannot connect to MariaDB at {MARIADB_HOST}:{MARIADB_PORT} "
            f"({exc.args[-1]}). Is the 'mariadb' service running?"
        ) from exc


class MariaDBStaging:
    """
    Copies MyISAM table files into the MariaDB data directory and reads them.

    The MariaDB container's datadir is shared with this service, so a database
    created through SQL can be filled with the user's .frm/.MYD/.MYI copies and
    queried like any other table.  The originals are never opened by MariaDB.
    """

    def __init__(self, database: str, reporter):
        self.database = database
        self.rep = reporter
        self.table_names = {}  # file stem -> table name as MariaDB reports it
        self._created = False

    # ---------- staging ----------

    def stage(self, tables) -> None:
        total = sum(t.size for t in tables)
        self.rep.begin("stage", total=total, detail=f"Copying {len(tables)} table(s) into MariaDB")

        with mariadb_connect() as conn, conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {_quote(self.database)}")
            cur.execute(f"CREATE DATABASE {_quote(self.database)} CHARACTER SET utf8mb4")
        self._created = True

        db_dir = os.path.join(MARIADB_DATADIR, self.database)
        if not os.path.isdir(db_dir):
            raise IngestError(
                f"MariaDB's data directory is not shared with the embedding service "
                f"({db_dir} not found). Check the mariadb_data volume in docker-compose.yml."
            )
        owner = os.stat(db_dir)

        copied = 0
        for table in tables:
            for ext, src in sorted(table.files.items()):
                dst = os.path.join(db_dir, table.name + MYISAM_EXTENSIONS[ext])
                copied = self._copy(src, dst, copied, total)
                os.chown(dst, owner.st_uid, owner.st_gid)
                os.chmod(dst, 0o660)

        with mariadb_connect(self.database) as conn, conn.cursor() as cur:
            cur.execute("FLUSH TABLES")
            cur.execute("SHOW TABLES")
            available = [row[0] for row in cur.fetchall()]

            for table in tables:
                name = self._match_table(table.name, available)
                if name is None:
                    raise IngestError(f"MariaDB could not open table '{table.name}'.")
                self.table_names[table.name] = name

                if ".myi" not in table.files:
                    self.rep.log(f"{table.name}: .MYI missing – rebuilding index from .frm")
                    self._repair(cur, name, use_frm=True)

        self.rep.finish("stage", detail=f"{len(tables)} table(s) loaded")

    def _copy(self, src, dst, copied, total) -> int:
        with open(src, "rb") as fin, open(dst, "wb") as fout:
            while True:
                buf = fin.read(8 * 1024 * 1024)
                if not buf:
                    break
                fout.write(buf)
                copied += len(buf)
                self.rep.progress(copied, total)
                self.rep.check_cancel()
        shutil.copystat(src, dst)
        return copied

    @staticmethod
    def _match_table(stem, available):
        # Names with characters MariaDB encodes in file names show up as #mysql50#name.
        for candidate in (stem, f"#mysql50#{stem}"):
            if candidate in available:
                return candidate
        lowered = {name.lower(): name for name in available}
        return lowered.get(stem.lower())

    def _repair(self, cur, name, use_frm=False):
        cur.execute(f"REPAIR TABLE {_quote(name)}" + (" USE_FRM" if use_frm else ""))
        for row in cur.fetchall():
            # (Table, Op, Msg_type, Msg_text)
            if str(row[2]).lower() == "error":
                raise IngestError(f"Repair of table '{name}' failed: {row[3]}")

    # ---------- reading ----------

    def count_rows(self, stem) -> int:
        with mariadb_connect(self.database) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {_quote(self.table_names[stem])}")
            return int(cur.fetchone()[0])

    def iter_papers(self, stem):
        import pymysql

        name = self.table_names[stem]

        with mariadb_connect(self.database) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SHOW COLUMNS FROM {_quote(name)}")
                columns = [row[0] for row in cur.fetchall()]

            mapping = map_columns(columns)
            if "abstract" not in mapping:
                self.rep.log(
                    f"{stem}: no abstract column found (columns: {', '.join(columns)}); table skipped",
                    level="warning",
                )
                return

            fields = list(mapping)
            sql = "SELECT {} FROM {}".format(
                ", ".join(_quote(mapping[f]) for f in fields), _quote(name)
            )

            for attempt in (1, 2):
                cur = conn.cursor(pymysql.cursors.SSCursor)
                try:
                    cur.execute(sql)
                    break
                except pymysql.err.MySQLError as exc:
                    cur.close()
                    if attempt == 2 or exc.args[0] not in _CRASHED_ERRORS:
                        raise IngestError(f"Cannot read table '{stem}': {exc.args[-1]}") from exc
                    self.rep.log(f"{stem}: table marked as crashed – repairing the staged copy")
                    with conn.cursor() as repair_cur:
                        self._repair(repair_cur, name)

            try:
                while True:
                    rows = cur.fetchmany(2000)
                    if not rows:
                        break
                    for row in rows:
                        yield make_paper(source=stem, **dict(zip(fields, row)))
            finally:
                cur.close()

    # ---------- cleanup ----------

    def drop(self) -> None:
        if not self._created:
            return
        try:
            with mariadb_connect(connect_timeout=5) as conn, conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS {_quote(self.database)}")
            self._created = False
        except Exception as exc:  # cleanup must never mask the real result
            print(f"⚠️ Could not drop staging database {self.database}: {exc}")
