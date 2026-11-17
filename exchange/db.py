import sqlite3

UNVERSIONED_STANDARD_ID = "unversioned"
UNVERSIONED_STANDARD_VERSION = "0"

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS standards (
    id TEXT NOT NULL, version TEXT NOT NULL, title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft','published','interrupted','withdrawn')),
    created_at TEXT NOT NULL, published_at TEXT, closed_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(id,version)
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, jurisdiction TEXT NOT NULL,
    classification TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    schema_version INTEGER NOT NULL DEFAULT 1,
    standard_id TEXT NOT NULL DEFAULT 'unversioned',
    standard_version TEXT NOT NULL DEFAULT '0'
);
CREATE TABLE IF NOT EXISTS fields (
    source_id TEXT NOT NULL REFERENCES sources(id),
    standard_id TEXT NOT NULL DEFAULT 'unversioned',
    standard_version TEXT NOT NULL DEFAULT '0',
    name TEXT NOT NULL, meaning TEXT NOT NULL, kind TEXT NOT NULL,
    sensitive INTEGER NOT NULL,
    PRIMARY KEY(source_id,standard_id,standard_version,name)
);
CREATE TABLE IF NOT EXISTS field_alignments (
    standard_id TEXT NOT NULL, from_version TEXT NOT NULL,
    to_version TEXT NOT NULL, field_name TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK(disposition IN ('reuse','recollect')),
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(standard_id,from_version,to_version,field_name)
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    schema_version INTEGER NOT NULL,
    standard_id TEXT NOT NULL DEFAULT 'unversioned',
    standard_version TEXT NOT NULL DEFAULT '0',
    payload TEXT NOT NULL, digest TEXT NOT NULL,
    received_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS grants (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    recipient TEXT NOT NULL, purpose TEXT NOT NULL, valid_until TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS policies (
    jurisdiction TEXT NOT NULL, classification TEXT NOT NULL, purpose TEXT NOT NULL,
    decision TEXT NOT NULL, revision INTEGER NOT NULL,
    PRIMARY KEY(jurisdiction,classification,purpose)
);
CREATE TABLE IF NOT EXISTS products (
    id TEXT NOT NULL, revision INTEGER NOT NULL, name TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL,
    standard_id TEXT NOT NULL DEFAULT 'unversioned',
    standard_version TEXT NOT NULL DEFAULT '0',
    basis TEXT NOT NULL DEFAULT '{}', comparable INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(id,revision)
);
CREATE TABLE IF NOT EXISTS product_inputs (
    product_id TEXT NOT NULL, product_revision INTEGER NOT NULL,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    standard_id TEXT NOT NULL DEFAULT 'unversioned',
    standard_version TEXT NOT NULL DEFAULT '0',
    PRIMARY KEY(product_id,product_revision,asset_id),
    FOREIGN KEY(product_id,product_revision) REFERENCES products(id,revision)
);
CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL, product_revision INTEGER NOT NULL,
    recipient TEXT NOT NULL, purpose TEXT NOT NULL, created_at TEXT NOT NULL,
    input_digest TEXT NOT NULL, policy_revisions TEXT NOT NULL,
    standard_id TEXT NOT NULL DEFAULT 'unversioned',
    standard_version TEXT NOT NULL DEFAULT '0',
    field_basis TEXT NOT NULL DEFAULT '{}',
    standard_status TEXT NOT NULL DEFAULT 'unregistered',
    conclusion_state TEXT NOT NULL DEFAULT 'issued',
    FOREIGN KEY(product_id,product_revision) REFERENCES products(id,revision)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL,
    action TEXT NOT NULL, subject TEXT NOT NULL, details TEXT NOT NULL
);
"""


def _column_names(connection, table):
    return {row[1] for row in connection.execute("PRAGMA table_info(" + table + ")")}


def _add_column(connection, table, definition):
    name = definition.split()[0]
    if name not in _column_names(connection, table):
        connection.execute("ALTER TABLE " + table + " ADD COLUMN " + definition)


def _migrate(connection):
    """Preserve old records while adding the standard-version lineage.

    Existing records did not carry a standard version. They are deliberately
    labelled ``unversioned/0`` instead of being guessed into a new criterion.
    """

    _add_column(connection, "sources", "standard_id TEXT NOT NULL DEFAULT 'unversioned'")
    _add_column(connection, "sources", "standard_version TEXT NOT NULL DEFAULT '0'")
    _add_column(connection, "assets", "standard_id TEXT NOT NULL DEFAULT 'unversioned'")
    _add_column(connection, "assets", "standard_version TEXT NOT NULL DEFAULT '0'")
    _add_column(connection, "products", "standard_id TEXT NOT NULL DEFAULT 'unversioned'")
    _add_column(connection, "products", "standard_version TEXT NOT NULL DEFAULT '0'")
    _add_column(connection, "products", "basis TEXT NOT NULL DEFAULT '{}'")
    _add_column(connection, "products", "comparable INTEGER NOT NULL DEFAULT 1")
    _add_column(connection, "product_inputs", "standard_id TEXT NOT NULL DEFAULT 'unversioned'")
    _add_column(connection, "product_inputs", "standard_version TEXT NOT NULL DEFAULT '0'")
    _add_column(connection, "deliveries", "standard_id TEXT NOT NULL DEFAULT 'unversioned'")
    _add_column(connection, "deliveries", "standard_version TEXT NOT NULL DEFAULT '0'")
    _add_column(connection, "deliveries", "field_basis TEXT NOT NULL DEFAULT '{}'")
    _add_column(connection, "deliveries", "standard_status TEXT NOT NULL DEFAULT 'unregistered'")
    _add_column(connection, "deliveries", "conclusion_state TEXT NOT NULL DEFAULT 'issued'")

    if "standard_id" not in _column_names(connection, "fields"):
        connection.executescript(
            "ALTER TABLE fields RENAME TO fields_unversioned;\n"
            "CREATE TABLE fields ("
            " source_id TEXT NOT NULL REFERENCES sources(id),"
            " standard_id TEXT NOT NULL DEFAULT 'unversioned',"
            " standard_version TEXT NOT NULL DEFAULT '0',"
            " name TEXT NOT NULL, meaning TEXT NOT NULL, kind TEXT NOT NULL,"
            " sensitive INTEGER NOT NULL,"
            " PRIMARY KEY(source_id,standard_id,standard_version,name));\n"
            "INSERT INTO fields(source_id,standard_id,standard_version,name,meaning,kind,sensitive)"
            " SELECT source_id,'unversioned','0',name,meaning,kind,sensitive FROM fields_unversioned;\n"
            "DROP TABLE fields_unversioned;\n"
        )

    # Product inputs kept their source asset identity, so their historical
    # standard can be recovered without reinterpreting the conclusion.
    connection.execute(
        "UPDATE product_inputs SET "
        "standard_id=COALESCE((SELECT a.standard_id FROM assets a WHERE a.id=asset_id),standard_id),"
        "standard_version=COALESCE((SELECT a.standard_version FROM assets a WHERE a.id=asset_id),standard_version) "
        "WHERE standard_id='unversioned' AND standard_version='0'"
    )


def connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    _migrate(connection)
    return connection
