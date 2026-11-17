import json

from .audit import write
from .db import UNVERSIONED_STANDARD_ID, UNVERSIONED_STANDARD_VERSION

STATUSES = ("draft", "published", "interrupted", "withdrawn")
FIELD_DISPOSITIONS = ("reuse", "recollect")


def is_versioned(standard_id, standard_version):
    return (standard_id, standard_version) != (
        UNVERSIONED_STANDARD_ID,
        UNVERSIONED_STANDARD_VERSION,
    )


class Standards:
    """评价标准版本及其字段口径。

    版本一旦发布，字段定义与字段沿用关系即冻结；需要调整时必须登记新版本。
    旧材料不因新版本发布而改变标签，历史结论也不会被重新解释。
    """

    def __init__(self, connection):
        self.connection = connection

    def register(self, standard_id, version, title, at, actor, note=""):
        if not standard_id or not version or not title:
            raise ValueError("评价标准编号、版本和名称不能为空")
        with self.connection:
            self.connection.execute(
                "INSERT INTO standards(id,version,title,status,created_at,note) VALUES(?,?,?,'draft',?,?)",
                (standard_id, version, title, at, note),
            )
            write(self.connection, at, actor, "standard.register", standard_id,
                  {"version": version, "title": title})
        return self.get(standard_id, version)

    def get(self, standard_id, version):
        row = self._row(standard_id, version)
        if row is None:
            raise KeyError((standard_id, version))
        return dict(row)

    def _row(self, standard_id, version):
        return self.connection.execute(
            "SELECT * FROM standards WHERE id=? AND version=?",
            (standard_id, version),
        ).fetchone()

    def status(self, standard_id, version):
        """返回标准状态；未登记时返回 None，避免把旧材料当成新口径。"""

        if not is_versioned(standard_id, version):
            return None
        row = self._row(standard_id, version)
        return row["status"] if row else None

    def _transition(self, standard_id, version, allowed, target, at, actor, action, note):
        row = self._row(standard_id, version)
        if row is None:
            raise KeyError((standard_id, version))
        if row["status"] not in allowed:
            raise ValueError("评价标准状态不能从 %s 变更为 %s" % (row["status"], target))
        with self.connection:
            self.connection.execute(
                "UPDATE standards SET status=?, note=?, published_at=CASE WHEN ?='published' AND published_at IS NULL THEN ? ELSE published_at END, "
                "closed_at=CASE WHEN ? IN ('interrupted','withdrawn') THEN ? ELSE closed_at END "
                "WHERE id=? AND version=?",
                (target, note, target, at, target, at, standard_id, version),
            )
            write(self.connection, at, actor, action, standard_id,
                  {"version": version, "from": row["status"], "to": target, "note": note})
        return self.get(standard_id, version)

    def publish(self, standard_id, version, at, actor):
        return self._transition(standard_id, version, {"draft"}, "published", at, actor,
                                "standard.publish", "")

    def interrupt(self, standard_id, version, at, actor, note=""):
        """暂停执行。已出具的结论保留原解释，但不允许再依据该版本出结论。"""

        return self._transition(standard_id, version, {"published"}, "interrupted", at, actor,
                                "standard.interrupt", note)

    def withdraw(self, standard_id, version, at, actor, note=""):
        """撤回版本。草案直接作废，已出具的结论保留原解释。"""

        return self._transition(standard_id, version, {"draft", "published", "interrupted"},
                                "withdrawn", at, actor, "standard.withdraw", note)

    def define_fields(self, standard_id, version):
        """登记字段定义前确认口径尚在草案阶段。"""

        if not is_versioned(standard_id, version):
            return
        row = self._row(standard_id, version)
        if row is None:
            raise ValueError("评价标准版本未登记")
        if row["status"] != "draft":
            raise ValueError("评价标准已发布或已关闭，字段口径须另立新版本")

    def align(self, standard_id, from_version, to_version, field_name, disposition, at, actor, note=""):
        if disposition not in FIELD_DISPOSITIONS:
            raise ValueError("字段沿用结论无效")
        if from_version == to_version:
            raise ValueError("沿用关系须跨版本登记")
        for version in (from_version, to_version):
            if self._row(standard_id, version) is None:
                raise KeyError((standard_id, version))
        target = self._row(standard_id, to_version)
        if target["status"] != "draft":
            raise ValueError("只能在草案版本上登记字段沿用关系")
        with self.connection:
            self.connection.execute(
                "INSERT INTO field_alignments(standard_id,from_version,to_version,field_name,disposition,note) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(standard_id,from_version,to_version,field_name) "
                "DO UPDATE SET disposition=excluded.disposition,note=excluded.note",
                (standard_id, from_version, to_version, field_name, disposition, note),
            )
            write(self.connection, at, actor, "standard.align", standard_id,
                  {"from": from_version, "to": to_version, "field": field_name,
                   "disposition": disposition, "note": note})

    def field_review(self, asset, standard_id, standard_version):
        """对照目标口径逐字段判断能否沿用。

        asset 可以是资产编号或资产字典。返回字段级状态：
        reuse（沿用）、recollect（重新取证）、retired（新口径不再采用）、
        collect（新口径要求但材料未提供）、unverified（来源未登记目标口径）。
        """

        if isinstance(asset, str):
            row = self.connection.execute("SELECT * FROM assets WHERE id=?", (asset,)).fetchone()
            if row is None:
                raise KeyError(asset)
            asset = dict(row)
        payload = json.loads(asset["payload"])
        from_id = asset["standard_id"]
        from_version = asset["standard_version"]
        same = (from_id, from_version) == (standard_id, standard_version)

        target_defs = {
            row["name"]: dict(row)
            for row in self.connection.execute(
                "SELECT * FROM fields WHERE source_id=? AND standard_id=? AND standard_version=?",
                (asset["source_id"], standard_id, standard_version),
            )
        }
        base = {
            "asset": asset["id"],
            "source": asset["source_id"],
            "from": {"standard_id": from_id, "standard_version": from_version},
            "to": {"standard_id": standard_id, "standard_version": standard_version},
            "target_available": bool(target_defs),
            "fields": {},
            "reuse": [],
            "recollect": [],
            "retired": [],
            "collect": [],
            "unverified": [],
        }

        if not target_defs and not same:
            base["fields"] = {name: {"status": "unverified", "note": "来源尚未登记该口径字段"}
                              for name in payload}
            base["unverified"] = sorted(payload)
            base["comparable"] = False
            return base

        old_defs = {
            row["name"]: dict(row)
            for row in self.connection.execute(
                "SELECT * FROM fields WHERE source_id=? AND standard_id=? AND standard_version=?",
                (asset["source_id"], from_id, from_version),
            )
        }
        alignments = {}
        if from_id == standard_id:
            alignments = {
                row["field_name"]: dict(row)
                for row in self.connection.execute(
                    "SELECT * FROM field_alignments WHERE standard_id=? AND from_version=? AND to_version=?",
                    (standard_id, from_version, standard_version),
                )
            }

        for name in sorted(payload):
            if name not in target_defs:
                status, note = "retired", "新口径未采用该字段，不能沿用"
            elif name in alignments:
                status = alignments[name]["disposition"]
                note = alignments[name]["note"] or (
                    "已登记为可沿用" if status == "reuse" else "已登记为需重新取证")
            elif same:
                status, note = "reuse", "同一口径，可直接沿用"
            else:
                old = old_defs.get(name)
                target = target_defs[name]
                if old and old["meaning"] == target["meaning"] and old["kind"] == target["kind"]:
                    status, note = "reuse", "字段定义未变，可沿用"
                else:
                    status, note = "recollect", "字段定义已变化，须按新口径重新取证"
            base["fields"][name] = {"status": status, "note": note}
            base[status].append(name)

        base["collect"] = sorted(name for name in target_defs if name not in payload)
        base["comparable"] = (
            not base["recollect"] and not base["retired"] and not base["collect"]
            and not base["unverified"]
        )
        return base

    def review(self, asset_id, standard_id, standard_version):
        """待审材料对照新口径的字段处置清单。"""

        if not is_versioned(standard_id, standard_version):
            raise ValueError("目标评价标准版本未登记")
        result = self.field_review(asset_id, standard_id, standard_version)
        result["target"] = dict(self.get(standard_id, standard_version))
        return result

    def impact(self, standard_id, version):
        """规则中断或撤回时，区分已出具结论与尚未发布的草案。"""

        standard = self.get(standard_id, version)
        state = standard["status"]
        issued = []
        for row in self.connection.execute(
            "SELECT * FROM deliveries WHERE standard_id=? AND standard_version=? ORDER BY id",
            (standard_id, version),
        ):
            item = dict(row)
            if state in ("interrupted", "withdrawn"):
                item["current_conclusion"] = "retained-original-interpretation"
            elif state == "published":
                item["current_conclusion"] = "effective"
            else:
                item["current_conclusion"] = "issued-before-closure"
            issued.append(item)

        drafts = []
        pending_state = {
            "draft": "unpublished-draft",
            "published": "draft-awaiting-delivery",
            "interrupted": "suspended-draft",
            "withdrawn": "withdrawn-draft",
        }[state]
        for row in self.connection.execute(
            "SELECT p.* FROM products p WHERE p.standard_id=? AND p.standard_version=? "
            "AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.product_id=p.id "
            "AND d.product_revision=p.revision) ORDER BY p.id,p.revision",
            (standard_id, version),
        ):
            item = dict(row)
            item["basis"] = json.loads(item["basis"])
            item["comparable"] = bool(item["comparable"])
            item["pending_state"] = pending_state
            drafts.append(item)

        return {
            "standard": standard,
            "issued": issued,
            "drafts": drafts,
            "interpretation": (
                "已出具的结论按原口径保留；尚未发布或尚未交付的草案不得当作新分数"
                if state in ("interrupted", "withdrawn")
                else "版本有效，可继续据此出具结论"
                if state == "published"
                else "版本尚未发布，草案仅供准备，不能出具结论"
            ),
        }
