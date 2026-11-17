import hashlib
import json

from .audit import write
from .grants import Grants
from .policy import Policies
from .products import Products
from .standards import is_versioned


class IncomparableInputs(ValueError):
    """输入材料的字段口径无法按目标评价标准沿用，必须重新取证。"""

    def __init__(self, basis):
        self.basis = basis
        fields = sorted({name for item in basis["inputs"] for name in item["recollect"]})
        super().__init__("输入材料的字段口径不可比，须重新取证: " + ", ".join(fields))


class Delivery:
    def __init__(self, connection):
        self.connection = connection
        self.grants = Grants(connection)
        self.policies = Policies(connection)
        self.products = Products(connection)

    def send(self, delivery_id, product_id, revision, recipient, purpose, at):
        existing = self.connection.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
        if existing:
            if (existing["product_id"], existing["product_revision"], existing["recipient"], existing["purpose"]) != (product_id, revision, recipient, purpose):
                raise ValueError("交付编号已用于其他请求")
            return self._receipt(existing)
        product = self.products.get(product_id, revision)
        if product["state"] != "draft":
            raise ValueError("数据产品修订不可交付")

        standard_id = product["standard_id"]
        standard_version = product["standard_version"]
        standard_status = None
        if is_versioned(standard_id, standard_version):
            standard = self.products.standards.get(standard_id, standard_version)
            standard_status = standard["status"]
            if standard_status != "published":
                raise ValueError("评价标准%s，不能据此出具新结论" % {
                    "draft": "尚未发布",
                    "interrupted": "已中断",
                    "withdrawn": "已撤回",
                }[standard_status])

        basis = product["basis"]
        if not product["comparable"]:
            with self.connection:
                write(self.connection, at, recipient, "delivery.reject", product_id,
                      {"revision": revision, "reasons": [{"source": "*", "reason": "incomparable"}],
                       "recollect": basis.get("recollect", [])})
            raise IncomparableInputs(basis)

        inputs = self.products.inputs(product_id, revision)
        if not inputs:
            raise ValueError("数据产品缺少输入")
        reasons, revisions = [], {}
        for asset in inputs:
            source_id = asset["source_id"]
            if asset["state"] != "active" or not asset["source_active"]:
                reasons.append({"source": source_id, "reason": "inactive"})
            if not self.grants.permits(source_id, recipient, purpose, at):
                reasons.append({"source": source_id, "reason": "grant"})
            decision, policy_revision = self.policies.decision(asset["jurisdiction"], asset["classification"], purpose)
            revisions[source_id] = policy_revision
            if decision != "allow":
                reasons.append({"source": source_id, "reason": "policy"})
        if reasons:
            with self.connection:
                write(self.connection, at, recipient, "delivery.reject", product_id, {"revision": revision, "reasons": reasons})
            raise PermissionError(reasons)
        digest = hashlib.sha256("|".join(asset["digest"] for asset in inputs).encode()).hexdigest()
        field_basis = {
            "standard_id": standard_id,
            "standard_version": standard_version,
            "standard_status": standard_status or "unregistered",
            "comparable": bool(product["comparable"]),
            "reuse": basis["reuse"],
            "recollect": basis["recollect"],
            "retired": basis["retired"],
            "collect": basis["collect"],
            "inputs": [
                {"asset": item["asset"], "from": item["from"], "to": item["to"],
                 "fields": item["fields"]}
                for item in basis["inputs"]
            ],
        }
        with self.connection:
            self.connection.execute(
                "INSERT INTO deliveries(id,product_id,product_revision,recipient,purpose,created_at,"
                "input_digest,policy_revisions,standard_id,standard_version,field_basis,"
                "standard_status,conclusion_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'issued')",
                (delivery_id, product_id, revision, recipient, purpose, at, digest,
                 json.dumps(revisions, sort_keys=True), standard_id, standard_version,
                 json.dumps(field_basis, ensure_ascii=False, sort_keys=True),
                 standard_status or "unregistered"),
            )
            write(self.connection, at, recipient, "delivery.accept", delivery_id,
                  {"product": product_id, "revision": revision,
                   "inputs": [a["id"] for a in inputs],
                   "standard_id": standard_id, "standard_version": standard_version})
        return self._receipt(self.connection.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone())

    def get(self, delivery_id):
        row = self.connection.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
        if row is None:
            raise KeyError(delivery_id)
        return self._receipt(row)

    def _receipt(self, row):
        """交付回执：结论依据的口径与字段沿用情况随回执固化。

        另附当前标准状态与当前结论状态。已出具的结论按原解释保留，
        标准中断或撤回不会改写历史回执，但复核人员能看出它与有效结论的差异。
        """

        receipt = dict(row)
        receipt["field_basis"] = json.loads(receipt["field_basis"]) if receipt["field_basis"] else {}
        receipt["policy_revisions"] = json.loads(receipt["policy_revisions"])
        basis = receipt["field_basis"]
        current_status = receipt["standard_status"]
        if is_versioned(receipt["standard_id"], receipt["standard_version"]):
            current_status = self.products.standards.status(
                receipt["standard_id"], receipt["standard_version"]) or "unregistered"
        receipt["current_standard_status"] = current_status
        receipt["current_conclusion_state"] = {
            "interrupted": "retained-original-interpretation",
            "withdrawn": "retained-original-interpretation",
            "published": "effective",
        }.get(current_status, receipt["conclusion_state"])
        receipt["basis"] = {
            "standard_id": receipt["standard_id"],
            "standard_version": receipt["standard_version"],
            "standard_status": receipt["standard_status"],
            "conclusion_state": receipt["conclusion_state"],
            "comparable": basis.get("comparable", False),
            "reuse": basis.get("reuse", []),
            "recollect": basis.get("recollect", []),
            "retired": basis.get("retired", []),
            "collect": basis.get("collect", []),
        }
        return receipt
