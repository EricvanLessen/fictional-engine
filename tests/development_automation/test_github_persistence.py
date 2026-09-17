from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from development_automation.github_persistence import GitHubControlBranchPersistence
from development_automation.live_adapters import ProviderRateLimitError, ProviderResponseError


class FakeGitHubControlApi:
    def __init__(self) -> None:
        self._blob_contents: dict[str, str] = {}
        self._trees: dict[str, dict[str, str]] = {}
        self._commits: dict[str, dict[str, str]] = {}
        self._refs: dict[str, str] = {}
        self._counter = 0
        self.fail_next_patch: tuple[str, str] | None = None
        self.seed_ref("heads/main", {})

    def _sha(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}{self._counter:036d}"

    def _blob_sha(self, content: str) -> str:
        for sha, existing in self._blob_contents.items():
            if existing == content:
                return sha
        sha = self._sha("b")
        self._blob_contents[sha] = content
        return sha

    def _tree_sha(self, files: dict[str, str]) -> str:
        tree = {path: self._blob_sha(content) for path, content in sorted(files.items())}
        sha = self._sha("t")
        self._trees[sha] = tree
        return sha

    def _commit_sha(self, tree_sha: str, parent_sha: str | None) -> str:
        sha = self._sha("c")
        payload = {"tree": tree_sha}
        if parent_sha is not None:
            payload["parent"] = parent_sha
        self._commits[sha] = payload
        return sha

    def seed_ref(self, ref: str, files: dict[str, str]) -> None:
        previous = self._refs.get(ref)
        tree_sha = self._tree_sha(files)
        commit_sha = self._commit_sha(tree_sha, previous)
        self._refs[ref] = commit_sha

    def files_for_ref(self, ref: str) -> dict[str, str]:
        commit_sha = self._refs[ref]
        tree_sha = self._commits[commit_sha]["tree"]
        return {
            path: self._blob_contents[blob_sha]
            for path, blob_sha in self._trees[tree_sha].items()
        }

    def append_to_ref(self, ref: str, path: str, line: str) -> None:
        files = self.files_for_ref(ref)
        files[path] = files.get(path, "") + line
        self.seed_ref(ref, files)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and "/git/ref/" in path:
            ref = path.split("/git/ref/", 1)[1]
            commit_sha = self._refs.get(ref)
            if commit_sha is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"object": {"sha": commit_sha}})
        if request.method == "GET" and "/git/commits/" in path:
            commit_sha = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"tree": {"sha": self._commits[commit_sha]["tree"]}})
        if request.method == "GET" and "/git/trees/" in path:
            tree_sha = path.split("/git/trees/", 1)[1].split("?", 1)[0]
            entries = [
                {"path": file_path, "sha": blob_sha, "type": "blob"}
                for file_path, blob_sha in self._trees[tree_sha].items()
            ]
            return httpx.Response(200, json={"tree": entries})
        if request.method == "GET" and "/git/blobs/" in path:
            blob_sha = path.rsplit("/", 1)[1]
            content = self._blob_contents[blob_sha]
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                },
            )
        if request.method == "POST" and path.endswith("/git/refs"):
            payload = json.loads(request.content.decode("utf-8"))
            ref = str(payload["ref"])[5:]
            self._refs.setdefault(ref, str(payload["sha"]))
            return httpx.Response(201, json={"ref": payload["ref"]})
        if request.method == "POST" and path.endswith("/git/trees"):
            payload = json.loads(request.content.decode("utf-8"))
            base_tree = self._trees[str(payload["base_tree"])].copy()
            for entry in payload["tree"]:
                base_tree[str(entry["path"])] = self._blob_sha(str(entry["content"]))
            tree_sha = self._sha("t")
            self._trees[tree_sha] = base_tree
            return httpx.Response(201, json={"sha": tree_sha})
        if request.method == "POST" and path.endswith("/git/commits"):
            payload = json.loads(request.content.decode("utf-8"))
            parent_sha = str(payload["parents"][0])
            commit_sha = self._commit_sha(str(payload["tree"]), parent_sha)
            return httpx.Response(201, json={"sha": commit_sha})
        if request.method == "PATCH" and "/git/refs/heads/" in path:
            ref = path.split("/git/refs/", 1)[1]
            if self.fail_next_patch is not None:
                conflict_path, conflict_line = self.fail_next_patch
                self.fail_next_patch = None
                self.append_to_ref(ref, conflict_path, conflict_line)
                return httpx.Response(422, json={"message": "Update is not a fast forward"})
            payload = json.loads(request.content.decode("utf-8"))
            next_commit = str(payload["sha"])
            patch_parent_sha: str | None = self._commits[next_commit].get("parent")
            if patch_parent_sha != self._refs.get(ref):
                return httpx.Response(422, json={"message": "Update is not a fast forward"})
            self._refs[ref] = next_commit
            return httpx.Response(200, json={"object": {"sha": next_commit}})
        raise AssertionError(f"unexpected request {request.method} {request.url}")


def test_control_branch_hydrate_and_sync_paths(tmp_path: Path) -> None:
    api = FakeGitHubControlApi()
    api.seed_ref(
        "heads/copilot/development-automation-control",
        {
            "control/messages/existing.md": "# existing\n",
            "control/runs/dispatcher-events.jsonl": '{"kind":"seed"}\n',
        },
    )
    repository_root = tmp_path / "repo"
    control_root = repository_root / "control"
    control_root.mkdir(parents=True, exist_ok=True)

    persistence = GitHubControlBranchPersistence(
        repository="EricvanLessen/fictional-engine",
        token="github-token",
        repository_root=repository_root,
        control_root=control_root,
        http_client=httpx.Client(transport=httpx.MockTransport(api.handler)),
    )

    persistence.hydrate()
    assert (control_root / "messages" / "existing.md").read_text(encoding="utf-8") == "# existing\n"

    new_message = control_root / "messages" / "new.md"
    new_message.parent.mkdir(parents=True, exist_ok=True)
    new_message.write_text("# new\n", encoding="utf-8")
    event_log = control_root / "runs" / "dispatcher-events.jsonl"
    event_log.parent.mkdir(parents=True, exist_ok=True)
    event_log.write_text('{"kind":"seed"}\n{"kind":"local"}\n', encoding="utf-8")

    persistence.sync_paths((new_message, event_log))

    files = api.files_for_ref("heads/copilot/development-automation-control")
    assert files["control/messages/new.md"] == "# new\n"
    assert files["control/runs/dispatcher-events.jsonl"] == '{"kind":"seed"}\n{"kind":"local"}\n'


def test_control_branch_sync_retries_ref_conflicts_and_merges_jsonl(tmp_path: Path) -> None:
    api = FakeGitHubControlApi()
    api.seed_ref(
        "heads/copilot/development-automation-control",
        {"control/runs/dispatcher-events.jsonl": '{"kind":"seed"}\n'},
    )
    api.fail_next_patch = ("control/runs/dispatcher-events.jsonl", '{"kind":"remote"}\n')

    repository_root = tmp_path / "repo"
    control_root = repository_root / "control"
    event_log = control_root / "runs" / "dispatcher-events.jsonl"
    event_log.parent.mkdir(parents=True, exist_ok=True)
    event_log.write_text('{"kind":"seed"}\n{"kind":"local"}\n', encoding="utf-8")

    persistence = GitHubControlBranchPersistence(
        repository="EricvanLessen/fictional-engine",
        token="github-token",
        repository_root=repository_root,
        control_root=control_root,
        http_client=httpx.Client(transport=httpx.MockTransport(api.handler)),
    )

    persistence.sync_paths((event_log,))

    assert event_log.read_text(encoding="utf-8") == (
        '{"kind":"seed"}\n{"kind":"remote"}\n{"kind":"local"}\n'
    )
    assert api.files_for_ref("heads/copilot/development-automation-control")[
        "control/runs/dispatcher-events.jsonl"
    ] == '{"kind":"seed"}\n{"kind":"remote"}\n{"kind":"local"}\n'


def test_control_branch_403_with_reset_header_is_not_treated_as_rate_limit(tmp_path: Path) -> None:
    repository_root = tmp_path / "repo"
    control_root = repository_root / "control"
    control_root.mkdir(parents=True, exist_ok=True)

    persistence = GitHubControlBranchPersistence(
        repository="EricvanLessen/fictional-engine",
        token="github-control-token",
        repository_root=repository_root,
        control_root=control_root,
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    403,
                    headers={
                        "x-ratelimit-remaining": "4999",
                        "x-ratelimit-reset": "9999999999",
                    },
                    json={"message": "Resource not accessible by integration"},
                )
            )
        ),
    )

    with pytest.raises(
        ProviderResponseError,
        match="/repos/EricvanLessen/fictional-engine/git/ref/heads/copilot/development-automation-control",
    ):
        persistence.hydrate()


def test_control_branch_403_with_zero_remaining_is_retryable_rate_limit(tmp_path: Path) -> None:
    repository_root = tmp_path / "repo"
    control_root = repository_root / "control"
    control_root.mkdir(parents=True, exist_ok=True)

    persistence = GitHubControlBranchPersistence(
        repository="EricvanLessen/fictional-engine",
        token="github-control-token",
        repository_root=repository_root,
        control_root=control_root,
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    403,
                    headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"},
                    json={"message": "secondary rate limit"},
                )
            )
        ),
    )

    with pytest.raises(ProviderRateLimitError):
        persistence.hydrate()
