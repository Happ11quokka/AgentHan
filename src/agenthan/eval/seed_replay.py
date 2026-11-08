"""Build a deterministic synthetic transport fixture from existing public M0 cases.

This is plumbing test data, not measured model performance. No credentials or network.
"""

import argparse
from pathlib import Path

from agenthan.agent.clova_chat import CHAT_MODEL
from agenthan.eval.cases import load_cases
from agenthan.eval.replay import Recorder, canonical
from agenthan.eval.run import MATCH_CASES, REPLAY_PATH, TOOL_CASES, evaluate
from agenthan.tools.feature_tools import all_tool_schemas


class Synthetic:
    model = CHAT_MODEL

    def __init__(self, cases):
        self.cases = {case.text: case for case in cases}
        self.schemas = {tool["function"]["name"]: tool["function"] for tool in all_tool_schemas()}

    def chat(self, messages, *, tools):
        tool = self.cases[messages[-1]["content"]].expected_tool
        if tool is None:
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

    def embed(self, text):
        # Deliberately constant: no oracle feature vectors or model-quality claim.
        return [1.0] + [0.0] * 1023


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPLAY_PATH)
    args = parser.parse_args(argv)
    cases = {"all_tools": load_cases(TOOL_CASES), "match": load_cases(MATCH_CASES)}
    recorder = Recorder(args.out, Synthetic(cases["all_tools"]), provenance="synthetic")
    evaluate(cases, recorder)
    args.out.write_text(canonical(recorder.data) + "\n", encoding="utf-8")
    print(f"Recorded {len(recorder.data['entries'])} requests ({recorder.data['provenance']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
