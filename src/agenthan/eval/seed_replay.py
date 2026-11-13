"""Build a deterministic synthetic transport fixture from existing public M0 cases.

This is plumbing test data, not measured model performance. No credentials or network.
"""

import argparse
from pathlib import Path

from agenthan.agent.clova_chat import CHAT_MODEL
from agenthan.agent.feature_selector import USER_TEMPLATE
from agenthan.eval.replay import Recorder, canonical
from agenthan.eval.run import DEFAULT_PATHS, REPLAY_PATH, evaluate, load_case_files, load_splits
from agenthan.tools.feature_tools import TOOL_FEATURES, all_tool_schemas


class Synthetic:
    model = CHAT_MODEL

    def __init__(self, cases):
        self.cases = {case.text: case for case in cases}
        self.schemas = {tool["function"]["name"]: tool["function"] for tool in all_tool_schemas()}

    def chat(self, messages, *, tools, tool_choice=None):
        case = self.cases.get(messages[-1]["content"]) if messages[-1]["role"] == "user" else None
        tool = case.expected_tool if case else None
        if tool is None or not tools:
            return {"role": "assistant", "content": "더미 응답: 입력을 확인해 주세요."}
        parameters = self.schemas[tool]["parameters"]
        arguments = {}
        for name in parameters["required"]:
            param = parameters["properties"][name]
            arguments[name] = (
                param.get("enum", [None])[0]
                if param.get("enum")
                else {
                    "string": "더미",
                    "integer": 1000,
                    "boolean": True,
                    "array": ["더미"],
                }[param["type"]]
            )
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "dummy", "function": {"name": tool, "arguments": canonical(arguments)}}
            ],
        }

    def select(self, messages, *, tools):
        prefix, suffix = USER_TEMPLATE.split("{text}")
        body = messages[-1]["content"].removeprefix(prefix)
        case = self.cases.get(body[: body.rfind(suffix)] if suffix in body else body)
        allowed = tools[0]["function"]["parameters"]["properties"]["feature_id"]["enum"]
        feature = (case.expected_feature or TOOL_FEATURES.get(case.expected_tool)) if case else None
        choice = feature if feature in allowed else "none"
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "select",
                    "function": {
                        "name": "select_feature",
                        "arguments": canonical({"feature_id": choice}),
                    },
                }
            ],
        }

    def embed(self, text):
        # Deliberately constant: no oracle feature vectors or model-quality claim.
        return [1.0] + [0.0] * 1023


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPLAY_PATH)
    args = parser.parse_args(argv)
    splits = load_splits()
    cases = {mode: load_case_files(files, splits=splits) for mode, files in DEFAULT_PATHS.items()}
    recorder = Recorder(args.out, Synthetic(cases["flow"] + cases["route"]), provenance="synthetic")
    evaluate(cases, recorder)
    args.out.write_text(canonical(recorder.data) + "\n", encoding="utf-8")
    print(f"Recorded {len(recorder.data['entries'])} requests ({recorder.data['provenance']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
