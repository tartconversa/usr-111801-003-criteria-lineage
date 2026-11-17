import json

from .audit import write
from .db import UNVERSIONED_STANDARD_ID, UNVERSIONED_STANDARD_VERSION
from .standards import Standards, is_versioned


class Products:
    def __init__(self, connection):
        self.connection = connection
        self.standards = Standards(connection)

    def assemble(self, product_id, name, asset_ids, at, actor,
                 standard_id=None, standard_version=None):
        """编排数据产品修订。

        产品修订记录其结论依据的评价标准版本。默认沿用首个输入原件的口径；
        显式指定时，所有输入须能按该口径字段沿用规则比较，否则标记为不可比。
        """

        asset_ids = list(dict.fromkeys(asset_ids))
        if not asset_ids:
            raise ValueError("数据产品缺少输入")
        placeholders = ",".join("?" for _ in asset_ids)
        found = self.connection.execute(
            "SELECT count(*) FROM assets WHERE id IN (" + placeholders + ") AND state='active'",
            asset_ids,
        ).fetchone()[0]
        if found != len(asset_ids):
            raise ValueError("输入记录不可用")

        assets = [
            dict(row) for row in self.connection.execute(
                "SELECT * FROM assets WHERE id IN (" + placeholders + ") ORDER BY id", asset_ids)
        ]
        if standard_id is None and standard_version is None:
            standard_id, standard_version = assets[0]["standard_id"], assets[0]["standard_version"]
        elif not standard_id or not standard_version:
            raise ValueError("评价标准编号和版本须同时提供")

        target_status = None
        if is_versioned(standard_id, standard_version):
            standard = self.standards.get(standard_id, standard_version)
            target_status = standard["status"]

        input_bases, comparable = [], True
        for asset in assets:
            review = self.standards.field_review(asset, standard_id, standard_version)
            if (asset["standard_id"], asset["standard_version"]) != (standard_id, standard_version):
                comparable = comparable and review["comparable"]
            input_bases.append({
                "asset": asset["id"],
                "source": asset["source_id"],
                "from": review["from"],
                "to": review["to"],
                "target_available": review["target_available"],
                "reuse": review["reuse"],
                "recollect": review["recollect"],
                "retired": review["retired"],
                "collect": review["collect"],
                "fields": review["fields"],
            })

        basis = {
            "standard": {"id": standard_id, "version": standard_version,
                         "status": target_status or "unregistered"},
            "standard_id": standard_id,
            "standard_version": standard_version,
            "comparable": comparable,
            "inputs": input_bases,
            "reuse": sorted({name for item in input_bases for name in item["reuse"]}),
            "recollect": sorted({name for item in input_bases for name in item["recollect"]}),
            "retired": sorted({name for item in input_bases for name in item["retired"]}),
            "collect": sorted({name for item in input_bases for name in item["collect"]}),
        }

        revision = self.connection.execute(
            "SELECT COALESCE(MAX(revision),0)+1 FROM products WHERE id=?", (product_id,)
        ).fetchone()[0]
        with self.connection:
            self.connection.execute(
                "INSERT INTO products(id,revision,name,created_at,standard_id,standard_version,basis,comparable) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (product_id, revision, name, at, standard_id, standard_version,
                 json.dumps(basis, ensure_ascii=False, sort_keys=True), int(comparable)),
            )
            self.connection.executemany(
                "INSERT INTO product_inputs(product_id,product_revision,asset_id,standard_id,standard_version) "
                "VALUES(?,?,?,?,?)",
                [(product_id, revision, item["asset"], item["from"]["standard_id"],
                  item["from"]["standard_version"]) for item in input_bases],
            )
            write(self.connection, at, actor, "product.assemble", product_id,
                  {"revision": revision, "inputs": asset_ids, "standard_id": standard_id,
                   "standard_version": standard_version, "comparable": comparable,
                   "recollect": basis["recollect"]})
        return revision

    def inputs(self, product_id, revision):
        rows = self.connection.execute(
            "SELECT a.*,p.standard_id AS input_standard_id,p.standard_version AS input_standard_version,"
            "s.owner,s.jurisdiction,s.classification,s.active AS source_active FROM product_inputs p "
            "JOIN assets a ON a.id=p.asset_id JOIN sources s ON s.id=a.source_id "
            "WHERE p.product_id=? AND p.product_revision=? ORDER BY a.id",
            (product_id, revision),
        ).fetchall()
        return [dict(row) for row in rows]

    def get(self, product_id, revision):
        row = self.connection.execute(
            "SELECT * FROM products WHERE id=? AND revision=?", (product_id, revision)
        ).fetchone()
        if row is None:
            raise KeyError((product_id, revision))
        result = dict(row)
        result["basis"] = json.loads(result["basis"])
        result["comparable"] = bool(result["comparable"])
        return result
