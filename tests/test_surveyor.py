"""Skill registration tests, including mixed-boundary multi-Tool Skills."""
from pathlib import Path
import tempfile
import unittest

from apex.defender.surveyor import Surveyor


class SkillRegistrationTests(unittest.TestCase):
    @staticmethod
    def _skill_manifest(name="mail-assistant", tools=None):
        return {"name": name, "description": "Trusted test Skill",
                "tools": (["read_record", "send_message", "delete_record"]
                          if tools is None else tools),
                "constraints": []}

    @staticmethod
    def _skill(root):
        path = Path(root, "mail-assistant", "SKILL.md")
        path.parent.mkdir()
        path.write_text(
            "---\nname: mail-assistant\n---\n"
            "# Mail Assistant\n"
            "Tools: `read_record`, `send_message`, and `delete_record`.\n",
            encoding="utf-8")
        return path

    @staticmethod
    def _manifest():
        return [
            {"skill": "mail-assistant", "name": "read_record",
             "description": "Read one record by id.",
             "arguments": ["record_id"], "required_arguments": ["record_id"],
             "argument_schemas": {"record_id": {"type": "string"}},
             "output_schema": {"type": "object", "properties": {
                 "body": {"type": "string"}}},
             "effect": False, "observation": True},
            {"skill": "mail-assistant", "name": "send_message",
             "description": "Send a message to one recipient.",
             "arguments": ["to", "body"],
             "required_arguments": ["to", "body"],
             "argument_schemas": {"to": {"type": "string"},
                                  "body": {"type": "string"}},
             "effect": True, "observation": False},
            {"skill": "mail-assistant", "name": "delete_record",
             "description": "Delete one record by id.",
             "arguments": ["record_id"], "required_arguments": ["record_id"],
             "argument_schemas": {"record_id": {"type": "string"}},
             "effect": True, "observation": False},
        ]

    def test_multitool_skill_has_tool_local_boundaries(self):
        with tempfile.TemporaryDirectory() as root:
            plan = Surveyor().perceive_skills(
                [self._skill(root)], self._manifest(),
                skill_manifests=[self._skill_manifest()])
        self.assertEqual(
            {"read_record", "send_message", "delete_record"},
            set(plan.capabilities))
        self.assertTrue(plan.capabilities["read_record"].observation)
        self.assertFalse(plan.capabilities["read_record"].effect)
        self.assertTrue(plan.capabilities["send_message"].effect)
        self.assertTrue(plan.capabilities["delete_record"].effect)
        self.assertEqual({"read_record"}, set(plan.sources))
        self.assertEqual(
            {"read_record", "send_message", "delete_record"},
            set(plan.skills["mail-assistant"].tools))

    def test_explicit_skill_manifest_is_separate_from_tool_boundaries(self):
        tools = [{
            "name": "write_file", "effect": True, "observation": True,
            "inputSchema": {"type": "object", "properties": {
                "path": {"type": "string"}}, "required": ["path"]},
        }]
        plan = Surveyor().perceive(tools, skill_manifests=[{
            "name": "documents",
            "description": "Transform document artifacts.",
            "tools": ["write_file"],
            "constraints": ["One isolated task workspace."],
        }])
        self.assertEqual(("write_file",), plan.skills["documents"].tools)
        self.assertEqual(
            ("One isolated task workspace.",),
            plan.skills["documents"].constraints)

    def test_duplicate_tool_name_across_skills_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            first = self._skill(root)
            second = Path(root, "other", "SKILL.md")
            second.parent.mkdir()
            second.write_text(
                "---\nname: other\n---\nTool: `read_record`.\n",
                encoding="utf-8")
            manifest = self._manifest() + [{
                "skill": "other", "name": "read_record",
                "required_arguments": [],
                "effect": False, "observation": True}]
            with self.assertRaisesRegex(ValueError, "duplicate capability"):
                Surveyor().perceive_skills([first, second], manifest)

    def test_manifest_tool_must_appear_in_owning_skill(self):
        with tempfile.TemporaryDirectory() as root:
            skill = self._skill(root)
            manifest = self._manifest()
            manifest[0] = {**manifest[0], "name": "invented_tool"}
            with self.assertRaisesRegex(ValueError, "does not declare Tool"):
                Surveyor().perceive_skills([skill], manifest)

    def test_prose_skill_can_register_control_and_state_plant_carriers(self):
        with tempfile.TemporaryDirectory() as root:
            skill = Path(root, "reviewer", "SKILL.md")
            skill.parent.mkdir()
            skill.write_text(
                "---\nname: reviewer\n---\nReview and issue findings.\n",
                encoding="utf-8")
            plan = Surveyor().perceive_skills(
                [skill], [], [
                    {"skill": "reviewer", "id": "clearance-boundary", "plantable": True,
                     "carrier": "control"},
                    {"skill": "reviewer", "id": "installable-skills", "plantable": True,
                     "carrier": "state"},
                ], skill_manifests=[self._skill_manifest("reviewer", [])])
        self.assertEqual("control", plan.sources["clearance-boundary"].carrier)
        self.assertEqual("state", plan.sources["installable-skills"].carrier)

    def test_unknown_plant_carrier_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown PLANT carrier"):
            Surveyor().perceive([], [{
                "id": "bad", "plantable": True, "carrier": "semantic"}])

    def test_unregistered_prose_skill_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as root:
            skill = Path(root, "reviewer", "SKILL.md")
            skill.parent.mkdir()
            skill.write_text(
                "---\nname: reviewer\n---\nReview findings.\n",
                encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no Skill 'reviewer'"):
                Surveyor().perceive_skills(
                    [skill], [], [{"skill": "other", "id": "boundary",
                                   "plantable": True, "carrier": "control"}])


if __name__ == "__main__":
    unittest.main()
