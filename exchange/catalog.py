import hashlib
import json

from .audit import write
from .db import UNVERSIONED_STANDARD_ID, UNVERSIONED_STANDARD_VERSION
from .standards import is_versioned


class Catalog:
    def __init__(self, connection):
        self.connection = connection

    def _standard(self, standard_id, standard_version):
        if standard_id is None and standard_version is None:
            return UNVERSIONED_STANDARD_ID, UNVERSIONED_STANDARD_VERSION
        if not standard_id or not standard_version:
            raise ValueError("评价标准编号和版本须同时提供")
        return standard_id, standard_version

    def _require_version(self, standard_id, standard_version):
        if not is_versioned(standard_id, standard_version):
            return
        row = self.connection.execute(
            "SELECT status FROM standards WHERE id=? AND version=?",
            (standard_id, standard_version),
        ).fetchone()
        if row is None:
            raise ValueError("来源字段须挂在已登记的评价标准版本上")
        return row["status"]

    def register(self, source_id, owner, jurisdiction, classification, fields, at,
                 standard_id=None, standard_version=None, actor=None):
        """登记来源字段目录。

        字段口径必须挂在具体评价标准版本上。未提供版本时以 unversioned/0
        登记，仅作为历史兼容材料，不参与与新口径的分数比较。
        """

        if not fields or len({field[0] for field in fields}) != len(fields):
            raise ValueError("来源字段缺失或重复")
        standard_id, standard_version = self._standard(standard_id, standard_version)
        status = self._require_version(standard_id, standard_version)
        if status is not None and status != "draft":
            raise ValueError("评价标准已发布或已关闭，字段口径须另立新版本")
        with self.connection:
            self.connection.execute(
                "INSERT INTO sources(id,owner,jurisdiction,classification,standard_id,standard_version) "
                "VALUES(?,?,?,?,?,?)",
                (source_id, owner, jurisdiction, classification, standard_id, standard_version),
            )
            self.connection.executemany(
                "INSERT INTO fields(source_id,standard_id,standard_version,name,meaning,kind,sensitive) "
                "VALUES(?,?,?,?,?,?,?)",
                [(source_id, standard_id, standard_version, name, meaning, kind, int(sensitive))
                 for name, meaning, kind, sensitive in fields],
            )
            write(self.connection, at, actor or owner, "source.register", source_id,
                  {"jurisdiction": jurisdiction, "standard_id": standard_id,
                   "standard_version": standard_version})
        return {"source": source_id, "standard_id": standard_id,
                "standard_version": standard_version}

    def add_version_fields(self, source_id, standard_id, standard_version, fields, at, actor):
        """为同一来源登记另一评价标准版本下的字段目录。"""

        if not fields or len({field[0] for field in fields}) != len(fields):
            raise ValueError("来源字段缺失或重复")
        source = self.connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if source is None:
            raise KeyError(source_id)
        status = self._require_version(standard_id, standard_version)
        if status != "draft":
            raise ValueError("评价标准已发布或已关闭，字段口径须另立新版本")
        existing = self.connection.execute(
            "SELECT 1 FROM fields WHERE source_id=? AND standard_id=? AND standard_version=? LIMIT 1",
            (source_id, standard_id, standard_version),
        ).fetchone()
        if existing:
            raise ValueError("该来源在此口径下的字段已登记")
        with self.connection:
            self.connection.executemany(
                "INSERT INTO fields(source_id,standard_id,standard_version,name,meaning,kind,sensitive) "
                "VALUES(?,?,?,?,?,?,?)",
                [(source_id, standard_id, standard_version, name, meaning, kind, int(sensitive))
                 for name, meaning, kind, sensitive in fields],
            )
            write(self.connection, at, actor, "source.fields", source_id,
                  {"standard_id": standard_id, "standard_version": standard_version,
                   "fields": [field[0] for field in fields]})
        return {"source": source_id, "standard_id": standard_id,
                "standard_version": standard_version}

    def fields(self, source_id, standard_id=None, standard_version=None):
        if standard_id is None and standard_version is None:
            source = self.connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            if source is None:
                raise KeyError(source_id)
            standard_id = source["standard_id"]
            standard_version = source["standard_version"]
        else:
            standard_id, standard_version = self._standard(standard_id, standard_version)
        rows = self.connection.execute(
            "SELECT * FROM fields WHERE source_id=? AND standard_id=? AND standard_version=? ORDER BY name",
            (source_id, standard_id, standard_version),
        ).fetchall()
        return [dict(row) for row in rows]

    def ingest(self, asset_id, source_id, payload, at, standard_id=None, standard_version=None):
        """登记原件。

        原件记录其采集时依据的评价标准版本；未显式给出时沿用来源目录当前版本。
        规则已中断或撤回的口径不再接受新取证。
        """

        source = self.connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if source is None or not source["active"]:
            raise ValueError("来源不可用")
        if standard_id is None and standard_version is None:
            standard_id, standard_version = source["standard_id"], source["standard_version"]
        else:
            standard_id, standard_version = self._standard(standard_id, standard_version)

        status = None
        if is_versioned(standard_id, standard_version):
            row = self.connection.execute(
                "SELECT status FROM standards WHERE id=? AND version=?",
                (standard_id, standard_version),
            ).fetchone()
            if row is None:
                raise ValueError("评价标准版本未登记，不能登记原件")
            status = row["status"]
            if status in ("interrupted", "withdrawn"):
                raise ValueError("评价标准已%s，不再接受新取证" % (
                    "中断" if status == "interrupted" else "撤回"))

        allowed = {
            row[0] for row in self.connection.execute(
                "SELECT name FROM fields WHERE source_id=? AND standard_id=? AND standard_version=?",
                (source_id, standard_id, standard_version),
            )
        }
        if not allowed:
            raise ValueError("来源未登记该评价标准版本的字段目录")
        if not isinstance(payload, dict) or not payload or set(payload) - allowed:
            raise ValueError("记录字段不符合来源目录")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self.connection:
            self.connection.execute(
                "INSERT INTO assets(id,source_id,schema_version,standard_id,standard_version,payload,digest,received_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (asset_id, source_id, source["schema_version"], standard_id, standard_version,
                 canonical, digest, at),
            )
            write(self.connection, at, source["owner"], "asset.ingest", asset_id,
                  {"source": source_id, "digest": digest, "standard_id": standard_id,
                   "standard_version": standard_version})
        return digest

    def get(self, asset_id):
        row = self.connection.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return dict(row)
