"""Standalone stdlib client for the agent process JSONL protocol."""

import json
import sys


class RemoteToolError(Exception):
    def __init__(self, error_type):
        self.error_type = error_type
        super().__init__(error_type)


class RemoteTools:
    def __init__(self):
        self.start = json.loads(sys.stdin.readline())
        if self.start.get("protocol") != "1":
            raise ValueError("Unsupported protocol")
        self._next_id = 0

    def call(self, name, **arguments):
        self._next_id += 1
        print(
            json.dumps(
                {"id": self._next_id, "method": "tool.call", "name": name, "arguments": arguments}
            ),
            flush=True,
        )
        response = json.loads(sys.stdin.readline())
        if response.get("id") != self._next_id:
            raise ValueError("Mismatched response ID")
        if "error" in response:
            raise RemoteToolError(response["error"]["type"])
        return response["result"]

    def finish(self, text, **metadata):
        print(json.dumps({"method": "result", "text": text, "metadata": metadata}), flush=True)
