from __future__ import annotations

import hashlib
import json

from .base import BenchmarkCase, DatasetAdapter


def _clean_id(server: str, query: str) -> str:
    return hashlib.sha256((server + "\0" + query).encode()).hexdigest()


class MCPToxAdapter(DatasetAdapter):
    benchmark = "MCPTox"
    slug = "mcptox"

    def cases(self, split=None):
        data = json.loads(self.json_path("cases.json").read_text(encoding="utf-8"))
        for server in data["servers"].values():
            name = str(server["server_name"])
            if split in (None, "clean"):
                for query in server["clean_querys"]:
                    query = str(query)
                    case_id = _clean_id(name, query)
                    yield BenchmarkCase(
                        self.benchmark, case_id, "clean", name,
                        {"query": query,
                         "system": str(server["clean_system_promot"])})
            if split in (None, "attack"):
                for instance in server["malicious_instance"]:
                    for row in instance.get("datas", []):
                        case_id = f"{name}:{row['id']}"
                        yield BenchmarkCase(
                            self.benchmark, case_id, "attack", name,
                            {"instance_id": row["id"],
                             "query": str(row.get("query", "")),
                             "system": str(row["system"]),
                             "clean_system": str(server["clean_system_promot"])})
