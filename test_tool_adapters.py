import unittest

from harness.agent_tool_eval import build_muse_request_payload, responses_output_text
from harness.tool_adapters import (
    build_codex_ptc_tools,
    build_hermes_tool,
    build_openai_responses_tool,
    load_tool_descriptor,
)


class ToolAdapterTests(unittest.TestCase):
    def test_openai_responses_wrapper_uses_strict_function_schema(self):
        tool = build_openai_responses_tool()

        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["name"], "colly_collect_context")
        self.assertTrue(tool["strict"])
        self.assertFalse(tool["parameters"]["additionalProperties"])

    def test_hermes_wrapper_only_changes_outer_envelope(self):
        responses = build_openai_responses_tool()
        hermes = build_hermes_tool()

        self.assertEqual(hermes["type"], "function")
        self.assertEqual(hermes["function"]["name"], responses["name"])
        self.assertEqual(
            hermes["function"]["parameters"], responses["parameters"]
        )

    def test_descriptor_description_is_lean(self):
        descriptor = load_tool_descriptor()
        description = descriptor["description"]

        self.assertLessEqual(len(description), 240)
        self.assertEqual(description.count("."), 1)

    def test_ptc_allowed_callers_do_not_change_parameters(self):
        direct = build_openai_responses_tool()
        ptc = build_openai_responses_tool(
            allowed_callers=["programmatic"]
        )

        self.assertEqual(ptc["parameters"], direct["parameters"])
        self.assertEqual(ptc["allowed_callers"], ["programmatic"])

    def test_codex_ptc_envelope_opts_function_into_programmatic_calls(self):
        tools = build_codex_ptc_tools()

        self.assertEqual(tools[0], {"type": "programmatic_tool_calling"})
        self.assertEqual(tools[1]["name"], "colly_collect_context")
        self.assertEqual(tools[1]["allowed_callers"], ["programmatic"])

    def test_responses_final_text_is_extracted_from_raw_rest_items(self):
        response = {
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "Artifacts written."},
                        {"type": "refusal", "refusal": "ignored"},
                    ],
                },
            ]
        }

        self.assertEqual(responses_output_text(response), "Artifacts written.")

    def test_muse_payload_records_seed_and_reasoning_strength(self):
        payload = build_muse_request_payload(
            model="meta-models/Muse-Glimmer-30B",
            prompt="Collect a.py",
            reasoning_effort="high",
            seed=20260811,
        )

        self.assertEqual(payload["seed"], 20260811)
        self.assertEqual(payload["model"], "meta-models/Muse-Glimmer-30B")
        self.assertIn("high", payload["messages"][0]["content"])
        self.assertEqual(payload["tools"], [build_hermes_tool()])


if __name__ == "__main__":
    unittest.main()
