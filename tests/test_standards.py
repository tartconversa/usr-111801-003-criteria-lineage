import sqlite3
import tempfile
import unittest
from pathlib import Path

from exchange import Exchange
from exchange.delivery import IncomparableInputs


def seed_v1(service, ingested_at="2026-03-02"):
    service.standards.register("GB-QA", "2022", "旅游景区质量等级划分", "2026-03-01", "标准组")
    service.standards.define_fields("GB-QA", "2022")
    service.catalog.register(
        "authority", "主管部门", "甲地", "review",
        [("hardware", "硬件设施", "score", False),
         ("service", "服务质量", "score", False),
         ("tourist_center", "游客中心", "score", False)],
        "2026-03-01", standard_id="GB-QA", standard_version="2022",
    )
    service.catalog.ingest("old-1", "authority",
                           {"hardware": 80, "service": 70, "tourist_center": 60},
                           ingested_at, standard_id="GB-QA", standard_version="2022")
    service.grants.issue("g-1", "authority", "复核组", "质量复核", "2027-12-31", "2026-03-01")
    service.policies.set("甲地", "review", "质量复核", "allow", "2026-03-01", "主管部门")
    service.standards.publish("GB-QA", "2022", "2026-03-03", "标准组")
    revision = service.products.assemble("dossier", "复核资料", ["old-1"], "2026-03-04",
                                         "资料室", standard_id="GB-QA", standard_version="2022")
    return revision


def define_v2(service):
    service.standards.register("GB-QA", "2026", "旅游景区质量评价（含服务与智慧旅游）",
                               "2026-06-01", "标准组")
    service.standards.define_fields("GB-QA", "2026")
    service.catalog.add_version_fields(
        "authority", "GB-QA", "2026",
        [("hardware", "硬件设施", "score", False),
         ("service", "服务质量（含人员与流程）", "score", False),
         ("smart_tourism", "智慧旅游", "score", False)],
        "2026-06-01", "资料室",
    )


class StandardLineageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Exchange(Path(self.temp.name) / "exchange.db")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_published_standard_is_recorded_on_source_product_and_receipt(self):
        revision = seed_v1(self.service)
        product = self.service.products.get("dossier", revision)
        self.assertEqual((product["standard_id"], product["standard_version"]), ("GB-QA", "2022"))
        self.assertEqual(product["basis"]["standard"]["status"], "published")

        receipt = self.service.delivery.send("d-1", "dossier", revision, "复核组",
                                             "质量复核", "2026-03-05")
        self.assertEqual(receipt["basis"]["standard_id"], "GB-QA")
        self.assertEqual(receipt["basis"]["standard_version"], "2022")
        self.assertEqual(receipt["basis"]["standard_status"], "published")
        self.assertTrue(receipt["basis"]["comparable"])
        self.assertEqual(receipt["field_basis"]["reuse"], ["hardware", "service", "tourist_center"])
        self.assertEqual(self.service.catalog.fields("authority", "GB-QA", "2022")[0]["standard_version"], "2022")

    def test_fields_cannot_change_after_publication(self):
        seed_v1(self.service)
        with self.assertRaises(ValueError):
            self.service.catalog.add_version_fields(
                "authority", "GB-QA", "2022", [("extra", "新增", "score", False)],
                "2026-03-06", "资料室")
        with self.assertRaises(ValueError):
            self.service.standards.align("GB-QA", "2022", "2022", "hardware", "reuse",
                                         "2026-03-06", "标准组")

    def test_review_separates_reusable_and_recollect_fields(self):
        seed_v1(self.service)
        define_v2(self.service)
        review = self.service.standards.review("old-1", "GB-QA", "2026")
        self.assertFalse(review["comparable"])
        self.assertEqual(review["reuse"], ["hardware"])
        self.assertEqual(review["recollect"], ["service"])
        self.assertEqual(review["retired"], ["tourist_center"])
        self.assertEqual(review["collect"], ["smart_tourism"])
        self.assertEqual(review["target"]["status"], "draft")
        self.assertEqual(review["from"], {"standard_id": "GB-QA", "standard_version": "2022"})
        self.assertIn("重新取证", review["fields"]["service"]["note"])

    def test_registered_alignment_overrides_definition_guess(self):
        seed_v1(self.service)
        define_v2(self.service)
        self.service.standards.align("GB-QA", "2022", "2026", "service", "reuse",
                                     "2026-06-02", "标准组", "复核口径确认，仅编号格式变化")
        review = self.service.standards.review("old-1", "GB-QA", "2026")
        self.assertEqual(review["reuse"], ["hardware", "service"])
        self.assertEqual(review["recollect"], [])

    def test_incomparable_material_cannot_become_new_score(self):
        seed_v1(self.service)
        define_v2(self.service)
        self.service.standards.publish("GB-QA", "2026", "2026-06-02", "标准组")
        revision = self.service.products.assemble(
            "dossier-v2", "按新口径复核", ["old-1"], "2026-06-03", "资料室",
            standard_id="GB-QA", standard_version="2026")
        product = self.service.products.get("dossier-v2", revision)
        self.assertFalse(product["comparable"])
        with self.assertRaises(IncomparableInputs):
            self.service.delivery.send("d-2", "dossier-v2", revision, "复核组",
                                       "质量复核", "2026-06-04")
        self.assertEqual(self.service.products.get("dossier-v2", revision)["state"], "draft")
        self.assertEqual(self.service.products.get("dossier-v2", revision)["basis"]["collect"], ["smart_tourism"])

    def test_draft_standard_cannot_be_used_for_conclusions(self):
        seed_v1(self.service)
        define_v2(self.service)
        revision = self.service.products.assemble(
            "dossier-v2", "按新口径复核", ["old-1"], "2026-06-03", "资料室",
            standard_id="GB-QA", standard_version="2026")
        self.assertFalse(self.service.products.get("dossier-v2", revision)["comparable"])
        with self.assertRaises(ValueError):
            self.service.delivery.send("d-2", "dossier-v2", revision, "复核组",
                                       "质量复核", "2026-06-04")
        self.service.catalog.ingest(
            "new-draft", "authority", {"hardware": 1}, "2026-06-03",
            standard_id="GB-QA", standard_version="2026")
        self.assertEqual(self.service.standards.status("GB-QA", "2026"), "draft")

    def test_withdrawal_keeps_issued_conclusion_and_marks_drafts(self):
        revision = seed_v1(self.service)
        receipt = self.service.delivery.send("d-1", "dossier", revision, "复核组",
                                             "质量复核", "2026-03-05")
        pending = self.service.products.assemble("awaiting", "待交付草稿", ["old-1"],
                                                 "2026-03-06", "资料室",
                                                 standard_id="GB-QA", standard_version="2022")
        self.service.standards.withdraw("GB-QA", "2022", "2026-06-10", "标准组", "新版替代")

        stored = self.service.delivery.send("d-1", "dossier", revision, "复核组",
                                            "质量复核", "2026-03-05")
        self.assertEqual(stored["field_basis"], receipt["field_basis"])
        self.assertEqual(stored["conclusion_state"], "issued")
        self.assertEqual(stored["basis"]["standard_status"], "published")
        self.assertEqual(stored["current_standard_status"], "withdrawn")
        self.assertEqual(stored["current_conclusion_state"], "retained-original-interpretation")

        with self.assertRaises(ValueError):
            self.service.delivery.send("d-2", "awaiting", pending, "复核组",
                                       "质量复核", "2026-06-11")
        impact = self.service.standards.impact("GB-QA", "2022")
        self.assertEqual([item["id"] for item in impact["issued"]], ["d-1"])
        self.assertEqual(impact["issued"][0]["current_conclusion"],
                         "retained-original-interpretation")
        self.assertEqual([(item["id"], item["pending_state"]) for item in impact["drafts"]],
                         [("awaiting", "withdrawn-draft")])
        self.assertIn("按原口径保留", impact["interpretation"])

    def test_interruption_blocks_new_conclusions_but_not_history(self):
        revision = seed_v1(self.service)
        self.service.delivery.send("d-1", "dossier", revision, "复核组", "质量复核", "2026-03-05")
        pending = self.service.products.assemble("awaiting", "待交付草稿", ["old-1"],
                                                 "2026-03-06", "资料室",
                                                 standard_id="GB-QA", standard_version="2022")
        self.service.standards.interrupt("GB-QA", "2022", "2026-04-01", "标准组", "项目定义调整中")
        with self.assertRaises(ValueError):
            self.service.delivery.send("d-2", "awaiting", pending, "复核组",
                                       "质量复核", "2026-04-02")
        with self.assertRaises(ValueError):
            self.service.catalog.ingest("new", "authority", {"hardware": 1}, "2026-04-02",
                                        standard_id="GB-QA", standard_version="2022")
        impact = self.service.standards.impact("GB-QA", "2022")
        self.assertEqual(impact["issued"][0]["id"], "d-1")
        self.assertEqual(impact["issued"][0]["current_conclusion"],
                         "retained-original-interpretation")
        self.assertEqual(impact["drafts"][0]["pending_state"], "suspended-draft")
        self.assertEqual(self.service.standards.status("GB-QA", "2022"), "interrupted")

    def test_unversioned_material_is_not_reinterpreted(self):
        self.service.catalog.register("legacy", "机构甲", "乙地", "restricted",
                                      [("count", "通行计数", "integer", False)], "2026-01-01")
        self.service.catalog.ingest("legacy-1", "legacy", {"count": 8}, "2026-01-02")
        asset = self.service.catalog.get("legacy-1")
        self.assertEqual((asset["standard_id"], asset["standard_version"]), ("unversioned", "0"))
        self.service.standards.register("GB-QA", "2026", "新口径", "2026-06-01", "标准组")
        self.service.standards.define_fields("GB-QA", "2026")
        self.service.standards.publish("GB-QA", "2026", "2026-06-02", "标准组")
        review = self.service.standards.review("legacy-1", "GB-QA", "2026")
        self.assertFalse(review["target_available"])
        self.assertEqual(review["unverified"], ["count"])
        self.assertFalse(review["comparable"])


class MigrationTest(unittest.TestCase):
    def test_existing_database_keeps_records_as_unversioned(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "old.db"
            old = sqlite3.connect(path)
            old.executescript(
                "CREATE TABLE sources (id TEXT PRIMARY KEY, owner TEXT NOT NULL,"
                " jurisdiction TEXT NOT NULL, classification TEXT NOT NULL,"
                " active INTEGER NOT NULL DEFAULT 1, schema_version INTEGER NOT NULL DEFAULT 1);"
                "CREATE TABLE fields (source_id TEXT NOT NULL REFERENCES sources(id),"
                " name TEXT NOT NULL, meaning TEXT NOT NULL, kind TEXT NOT NULL,"
                " sensitive INTEGER NOT NULL, PRIMARY KEY(source_id,name));"
                "CREATE TABLE assets (id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),"
                " schema_version INTEGER NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,"
                " received_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active');"
                "CREATE TABLE grants (id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),"
                " recipient TEXT NOT NULL, purpose TEXT NOT NULL, valid_until TEXT NOT NULL,"
                " status TEXT NOT NULL DEFAULT 'active');"
                "CREATE TABLE policies (jurisdiction TEXT NOT NULL, classification TEXT NOT NULL,"
                " purpose TEXT NOT NULL, decision TEXT NOT NULL, revision INTEGER NOT NULL,"
                " PRIMARY KEY(jurisdiction,classification,purpose));"
                "CREATE TABLE products (id TEXT NOT NULL, revision INTEGER NOT NULL, name TEXT NOT NULL,"
                " state TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL,"
                " PRIMARY KEY(id,revision));"
                "CREATE TABLE product_inputs (product_id TEXT NOT NULL, product_revision INTEGER NOT NULL,"
                " asset_id TEXT NOT NULL REFERENCES assets(id),"
                " PRIMARY KEY(product_id,product_revision,asset_id),"
                " FOREIGN KEY(product_id,product_revision) REFERENCES products(id,revision));"
                "CREATE TABLE deliveries (id TEXT PRIMARY KEY, product_id TEXT NOT NULL,"
                " product_revision INTEGER NOT NULL, recipient TEXT NOT NULL, purpose TEXT NOT NULL,"
                " created_at TEXT NOT NULL, input_digest TEXT NOT NULL, policy_revisions TEXT NOT NULL,"
                " FOREIGN KEY(product_id,product_revision) REFERENCES products(id,revision));"
                "CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,"
                " actor TEXT NOT NULL, action TEXT NOT NULL, subject TEXT NOT NULL, details TEXT NOT NULL);"
                "INSERT INTO sources VALUES('legacy','机构甲','甲地','review',1,1);"
                "INSERT INTO fields VALUES('legacy','finding','服务观察','text',0);"
                "INSERT INTO assets VALUES('item-1','legacy',1,'{\"finding\":\"指引可见\"}','abc','2026-01-02','active');"
                "INSERT INTO products VALUES('dossier',1,'旧结论','draft','2026-01-03');"
                "INSERT INTO product_inputs VALUES('dossier',1,'item-1');"
                "INSERT INTO deliveries VALUES('d-1','dossier',1,'复核组','质量复核','2026-01-04',"
                "'digest','{\"legacy\":1}');"
            )
            old.commit()
            old.close()

            service = Exchange(path)
            try:
                asset = service.catalog.get("item-1")
                self.assertEqual(asset["standard_id"], "unversioned")
                self.assertEqual(asset["standard_version"], "0")
                self.assertEqual(service.catalog.fields("legacy")[0]["name"], "finding")
                receipt = service.delivery.send("d-1", "dossier", 1, "复核组",
                                                "质量复核", "2026-01-04")
                self.assertEqual(receipt["conclusion_state"], "issued")
                self.assertEqual(receipt["standard_status"], "unregistered")
                self.assertTrue(service.history("item-1") == [])
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
