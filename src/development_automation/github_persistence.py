from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx

from development_automation.live_adapters import (
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)


@dataclass(frozen=True)
class GitRefState:
    commit_sha: str
    tree_sha: str


@dataclass(frozen=True)
class GitTreeEntry:
    path: str
    sha: str
    type: str


class GitHubControlBranchPersistence:
    def __init__(
        self,
        *,
        repository: str,
        token: str,
        repository_root: Path,
        control_root: Path,
        branch: str = "copilot/development-automation-control",
        base_ref: str = "refs/heads/main",
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
        http_client: httpx.Client | None = None,
        max_ref_update_attempts: int = 4,
    ) -> None:
        if "/" not in repository:
            raise ValueError("repository must be owner/name")
        owner, repo = repository.split("/", 1)
        self._owner = owner
        self._repo = repo
        self._token = token
        self._repository_root = repository_root.resolve()
        self._control_root = control_root.resolve()
        self._branch = branch
        self._base_ref = base_ref
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._max_ref_update_attempts = max_ref_update_attempts
        self._control_prefix = self._control_root.relative_to(self._repository_root).as_posix()

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + self._token,
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
    ) -> httpx.Response:
        try:
            response = self._client.request(
                method,
                f"{self._base_url}{path}",
                headers=self._headers(),
                json=json_body,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("GitHub control-state request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("GitHub control-state request failed") from exc
        if response.status_code == 429:
            raise ProviderRateLimitError("GitHub control-state request was rate limited")
        if response.status_code == 403 and (
            response.headers.get("x-ratelimit-remaining") == "0"
            or response.headers.get("retry-after") is not None
        ):
            raise ProviderRateLimitError("GitHub control-state request was rate limited")
        if response.status_code >= 400:
            raise ProviderResponseError(
                f"GitHub returned HTTP {response.status_code} for {path}"
            )
        return response

    @staticmethod
    def _ref_path(ref: str) -> str:
        return ref[5:] if ref.startswith("refs/") else ref

    def _get_ref_state(self, *, create_if_missing: bool) -> GitRefState | None:
        try:
            response = self._request(
                "GET",
                f"/repos/{self._owner}/{self._repo}/git/ref/heads/{self._branch}",
            )
        except ProviderResponseError as exc:
            if "HTTP 404" not in str(exc):
                raise
            if not create_if_missing:
                return None
            base_ref = self._request(
                "GET",
                f"/repos/{self._owner}/{self._repo}/git/ref/{self._ref_path(self._base_ref)}",
            ).json()
            if not isinstance(base_ref, dict):
                raise ProviderResponseError("GitHub ref response must be an object") from None
            base_object = base_ref.get("object")
            if not isinstance(base_object, dict) or not isinstance(base_object.get("sha"), str):
                raise ProviderResponseError(
                    "GitHub ref response is missing commit sha"
                ) from None
            try:
                self._request(
                    "POST",
                    f"/repos/{self._owner}/{self._repo}/git/refs",
                    json_body={
                        "ref": f"refs/heads/{self._branch}",
                        "sha": str(base_object["sha"]),
                    },
                )
            except ProviderResponseError as create_exc:
                if "HTTP 422" not in str(create_exc):
                    raise
            response = self._request(
                "GET",
                f"/repos/{self._owner}/{self._repo}/git/ref/heads/{self._branch}",
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderResponseError("GitHub ref response must be an object")
        obj = payload.get("object")
        if not isinstance(obj, dict) or not isinstance(obj.get("sha"), str):
            raise ProviderResponseError("GitHub ref response is missing commit sha")
        commit_sha = str(obj["sha"])
        commit = self._request(
            "GET",
            f"/repos/{self._owner}/{self._repo}/git/commits/{commit_sha}",
        ).json()
        if not isinstance(commit, dict):
            raise ProviderResponseError("GitHub commit response must be an object")
        tree = commit.get("tree")
        if not isinstance(tree, dict) or not isinstance(tree.get("sha"), str):
            raise ProviderResponseError("GitHub commit response is missing tree sha")
        return GitRefState(commit_sha=commit_sha, tree_sha=str(tree["sha"]))

    def _tree_entries(self, tree_sha: str) -> dict[str, GitTreeEntry]:
        payload = self._request(
            "GET",
            f"/repos/{self._owner}/{self._repo}/git/trees/{tree_sha}?recursive=1",
        ).json()
        if not isinstance(payload, dict):
            raise ProviderResponseError("GitHub tree response must be an object")
        entries = payload.get("tree")
        if not isinstance(entries, list):
            raise ProviderResponseError("GitHub tree response is missing entries")
        result: dict[str, GitTreeEntry] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            sha = entry.get("sha")
            entry_type = entry.get("type")
            if (
                not isinstance(path, str)
                or not isinstance(sha, str)
                or not isinstance(entry_type, str)
            ):
                continue
            result[path] = GitTreeEntry(path=path, sha=sha, type=entry_type)
        return result

    def _blob_text(self, sha: str) -> str:
        payload = self._request(
            "GET",
            f"/repos/{self._owner}/{self._repo}/git/blobs/{sha}",
        ).json()
        if not isinstance(payload, dict):
            raise ProviderResponseError("GitHub blob response must be an object")
        content = payload.get("content")
        encoding = payload.get("encoding")
        if not isinstance(content, str) or encoding != "base64":
            raise ProviderResponseError("GitHub blob response is missing base64 content")
        normalized = content.replace("\n", "")
        return base64.b64decode(normalized).decode("utf-8")

    def hydrate(self) -> None:
        ref_state = self._get_ref_state(create_if_missing=False)
        if ref_state is None:
            return
        for entry in self._tree_entries(ref_state.tree_sha).values():
            if entry.type != "blob":
                continue
            if entry.path != self._control_prefix and not entry.path.startswith(
                f"{self._control_prefix}/"
            ):
                continue
            destination = self._repository_root / PurePosixPath(entry.path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(self._blob_text(entry.sha), encoding="utf-8")

    def _relative_paths(self, paths: tuple[Path, ...]) -> tuple[str, ...]:
        relative_paths = []
        for path in paths:
            resolved = path.resolve()
            relative = resolved.relative_to(self._repository_root).as_posix()
            if relative != self._control_prefix and not relative.startswith(
                f"{self._control_prefix}/"
            ):
                raise ProviderResponseError("refusing to persist files outside the control root")
            relative_paths.append(relative)
        return tuple(sorted(dict.fromkeys(relative_paths)))

    @staticmethod
    def _merge_jsonl(remote: str, local: str) -> str:
        remote_lines = [line for line in remote.splitlines() if line.strip()]
        merged_lines = list(remote_lines)
        for line in local.splitlines():
            if line.strip() and line not in remote_lines:
                merged_lines.append(line)
        return ("\n".join(merged_lines) + "\n") if merged_lines else ""

    def _reconcile_local_files(
        self,
        relative_paths: tuple[str, ...],
        remote_entries: dict[str, GitTreeEntry],
    ) -> dict[str, str]:
        reconciled: dict[str, str] = {}
        for relative_path in relative_paths:
            local_path = self._repository_root / PurePosixPath(relative_path)
            if not local_path.exists():
                raise ProviderResponseError(f"local control file is missing: {relative_path}")
            local_content = local_path.read_text(encoding="utf-8")
            remote_entry = remote_entries.get(relative_path)
            if remote_entry is None:
                reconciled[relative_path] = local_content
                continue
            remote_content = self._blob_text(remote_entry.sha)
            if relative_path.endswith(".jsonl"):
                merged = self._merge_jsonl(remote_content, local_content)
                if merged != local_content:
                    local_path.write_text(merged, encoding="utf-8")
                reconciled[relative_path] = merged
                continue
            if remote_content != local_content:
                raise ProviderResponseError(f"append-only control file conflict at {relative_path}")
            reconciled[relative_path] = local_content
        return reconciled

    def _create_tree(self, base_tree_sha: str, contents_by_path: dict[str, str]) -> str:
        tree_entries = []
        for path, content in contents_by_path.items():
            tree_entries.append(
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "content": content,
                }
            )
        payload = self._request(
            "POST",
            f"/repos/{self._owner}/{self._repo}/git/trees",
            json_body={"base_tree": base_tree_sha, "tree": tree_entries},
        ).json()
        if not isinstance(payload, dict) or not isinstance(payload.get("sha"), str):
            raise ProviderResponseError("GitHub tree creation response is missing sha")
        return str(payload["sha"])

    def _create_commit(self, tree_sha: str, parent_sha: str) -> str:
        payload = self._request(
            "POST",
            f"/repos/{self._owner}/{self._repo}/git/commits",
            json_body={
                "message": "Persist development automation control state",
                "tree": tree_sha,
                "parents": [parent_sha],
            },
        ).json()
        if not isinstance(payload, dict) or not isinstance(payload.get("sha"), str):
            raise ProviderResponseError("GitHub commit creation response is missing sha")
        return str(payload["sha"])

    def _update_ref(self, commit_sha: str) -> None:
        self._request(
            "PATCH",
            f"/repos/{self._owner}/{self._repo}/git/refs/heads/{self._branch}",
            json_body={"sha": commit_sha, "force": False},
        )

    def sync_paths(self, paths: tuple[Path, ...]) -> None:
        relative_paths = self._relative_paths(paths)
        if not relative_paths:
            return
        for _ in range(self._max_ref_update_attempts):
            ref_state = self._get_ref_state(create_if_missing=True)
            if ref_state is None:
                raise ProviderResponseError("control branch ref could not be resolved")
            remote_entries = self._tree_entries(ref_state.tree_sha)
            contents_by_path = self._reconcile_local_files(relative_paths, remote_entries)
            tree_sha = self._create_tree(ref_state.tree_sha, contents_by_path)
            commit_sha = self._create_commit(tree_sha, ref_state.commit_sha)
            try:
                self._update_ref(commit_sha)
                return
            except ProviderResponseError as exc:
                if "HTTP 422" not in str(exc):
                    raise
        raise ProviderResponseError(
            "control branch update lost too many optimistic-concurrency races"
        )
