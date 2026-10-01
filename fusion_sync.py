#!/usr/bin/env python3
"""
fusion_sync.py - keep a local, browsable mirror of everything in your Autodesk
Fusion 360 cloud hubs.

Fusion designs are not files; they live as versioned records in Autodesk's
cloud (Fusion Team / Autodesk Docs). This tool walks every hub -> project ->
folder you can see through the Autodesk Platform Services (APS) Data
Management API, asks the cloud to export each design's latest version to a
real file (native .f3d/.f3z by default, optionally STEP/STL/etc.), and writes
the result into a folder tree that mirrors the cloud. Non-Fusion files that
were uploaded to a project (PDFs, STLs, DXFs, ...) are downloaded as-is.

Runs are incremental: a manifest remembers which cloud version each local
file came from, so re-running only fetches what changed.

Standard library only. Python 3.9+.

Quick start:
    ./fusion_sync.py init --client-id <APS_CLIENT_ID> --root ~/FusionCAD
    ./fusion_sync.py auth              # opens browser once, caches refresh token
    ./fusion_sync.py hubs              # sanity check
    ./fusion_sync.py sync              # mirror everything
    ./fusion_sync.py sync --formats native,step
    ./fusion_sync.py watch --interval 900
    ./fusion_sync.py install-launchd --interval 900   # run in background on macOS

Register the APS app at https://aps.autodesk.com/myapps (type "Desktop, Mobile,
Single-Page App", callback URL http://localhost:8912/callback, Data Management
API enabled). PKCE is used, so no client secret is needed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

APS_BASE = "https://developer.api.autodesk.com"
AUTH_URL = f"{APS_BASE}/authentication/v2/authorize"
TOKEN_URL = f"{APS_BASE}/authentication/v2/token"
DEFAULT_SCOPES = "data:read data:create"
DEFAULT_CALLBACK_PORT = 8912
CALLBACK_PATH = "/callback"

CONFIG_DIR = Path(os.environ.get("FUSION_SYNC_CONFIG_DIR", Path.home() / ".config" / "fusion-sync"))
CONFIG_PATH = CONFIG_DIR / "config.json"
TOKEN_PATH = CONFIG_DIR / "tokens.json"

STATE_DIRNAME = ".fusion-sync"
MANIFEST_NAME = "manifest.json"
VERSIONS_DIRNAME = "_versions"

# Preferred export formats per Fusion item kind when the user asks for "native".
# f3z is the archive form used when a design references external components.
NATIVE_PREFERENCE = {
    "design": ["f3z", "f3d"],
    "drawing": ["pdf", "dwg"],
    "cam": ["f3d"],
    "other": [],
}

JOB_POLL_SECONDS = 3
JOB_TIMEOUT_SECONDS = 15 * 60


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def log(msg: str, *, err: bool = False) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {msg}", file=sys.stderr if err else sys.stdout, flush=True)


_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(name: str) -> str:
    """Make a cloud display name safe as a single path component."""
    cleaned = _UNSAFE.sub("_", name).strip().rstrip(".")
    return cleaned or "_unnamed"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def write_json(path: Path, data: Any, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

@dataclass
class Config:
    client_id: str
    root: Path
    client_secret: Optional[str] = None
    callback_port: int = DEFAULT_CALLBACK_PORT
    scopes: str = DEFAULT_SCOPES
    formats: List[str] = field(default_factory=lambda: ["native"])
    hubs: List[str] = field(default_factory=list)      # optional name filters
    projects: List[str] = field(default_factory=list)  # optional name filters
    keep_versions: bool = True

    @property
    def redirect_uri(self) -> str:
        return f"http://localhost:{self.callback_port}{CALLBACK_PATH}"

    @classmethod
    def load(cls) -> "Config":
        raw = read_json(CONFIG_PATH)
        if not raw:
            sys.exit(f"No config at {CONFIG_PATH}. Run: fusion_sync.py init --client-id ... --root ...")
        raw["root"] = Path(raw["root"]).expanduser()
        return cls(**raw)

    def save(self) -> None:
        data = self.__dict__.copy()
        data["root"] = str(self.root)
        write_json(CONFIG_PATH, data, private=True)


# --------------------------------------------------------------------------- #
# OAuth (3-legged, PKCE)
# --------------------------------------------------------------------------- #

class Auth:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tokens: Dict[str, Any] = read_json(TOKEN_PATH, {}) or {}

    # -- token lifecycle ---------------------------------------------------- #

    def access_token(self) -> str:
        if not self.tokens:
            sys.exit("Not authenticated. Run: fusion_sync.py auth")
        if time.time() > self.tokens.get("expires_at", 0) - 60:
            self._refresh()
        return self.tokens["access_token"]

    def _token_request(self, form: Dict[str, str]) -> None:
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        if self.cfg.client_secret:
            basic = base64.b64encode(f"{self.cfg.client_id}:{self.cfg.client_secret}".encode()).decode()
            headers["Authorization"] = f"Basic {basic}"
        else:
            form["client_id"] = self.cfg.client_id
        req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(form).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            sys.exit(f"Token request failed ({exc.code}): {detail}")
        body["expires_at"] = time.time() + int(body.get("expires_in", 3600))
        self.tokens = body
        write_json(TOKEN_PATH, body, private=True)

    def _refresh(self) -> None:
        refresh = self.tokens.get("refresh_token")
        if not refresh:
            sys.exit("Access token expired and no refresh token stored. Run: fusion_sync.py auth")
        log("Refreshing access token")
        self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "scope": self.cfg.scopes,
        })

    # -- interactive login -------------------------------------------------- #

    def login(self, *, open_browser: bool = True) -> None:
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        params = {
            "response_type": "code",
            "client_id": self.cfg.client_id,
            "redirect_uri": self.cfg.redirect_uri,
            "scope": self.cfg.scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "login",
        }
        url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

        result: Dict[str, str] = {}
        done = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):  # silence default logging
                pass

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != CALLBACK_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                result.update({k: v[0] for k, v in qs.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                ok = "code" in qs and qs.get("state", [""])[0] == state
                msg = "Signed in. You can close this tab." if ok else "Sign-in failed; check the terminal."
                self.wfile.write(f"<html><body style='font-family:sans-serif;padding:2em'><h2>{msg}</h2></body></html>".encode())
                done.set()

        server = http.server.HTTPServer(("127.0.0.1", self.cfg.callback_port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        log("Open this URL in your browser to sign in to Autodesk:")
        print(url, flush=True)
        if open_browser:
            webbrowser.open(url)

        if not done.wait(timeout=600):
            server.shutdown()
            sys.exit("Timed out waiting for the browser callback.")
        server.shutdown()

        if result.get("state") != state:
            sys.exit("State mismatch in OAuth callback; aborting.")
        if "code" not in result:
            sys.exit(f"Authorization denied: {result}")

        self._token_request({
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": self.cfg.redirect_uri,
            "code_verifier": verifier,
        })
        log(f"Authenticated. Tokens cached at {TOKEN_PATH}")


# --------------------------------------------------------------------------- #
# APS Data Management client (just what we need)
# --------------------------------------------------------------------------- #

class ApiError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} for {url}: {body[:400]}")
        self.status = status
        self.body = body


class Aps:
    def __init__(self, auth: Auth):
        self.auth = auth

    def _request(self, method: str, url: str, *, body: Any = None, headers: Optional[Dict[str, str]] = None,
                 auth: bool = True, retries: int = 5) -> urllib.response.addinfourl:
        hdrs = {"Accept": "application/vnd.api+json, application/json"}
        if auth:
            hdrs["Authorization"] = f"Bearer {self.auth.access_token()}"
        if headers:
            hdrs.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/vnd.api+json"
        for attempt in range(retries):
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            try:
                return urllib.request.urlopen(req, timeout=120)
            except urllib.error.HTTPError as exc:
                text = exc.read().decode(errors="replace")
                if exc.code == 429 or exc.code >= 500:
                    wait = float(exc.headers.get("Retry-After") or (2 ** attempt))
                    log(f"HTTP {exc.code} from {url}; retrying in {wait:.0f}s", err=True)
                    time.sleep(wait)
                    continue
                if exc.code == 401 and auth and attempt == 0:
                    self.auth._refresh()
                    hdrs["Authorization"] = f"Bearer {self.auth.access_token()}"
                    continue
                raise ApiError(exc.code, text, url) from None
            except urllib.error.URLError as exc:
                if attempt == retries - 1:
                    raise
                log(f"Network error ({exc.reason}); retrying", err=True)
                time.sleep(2 ** attempt)
        raise ApiError(0, "retries exhausted", url)

    def get(self, url: str) -> Dict[str, Any]:
        with self._request("GET", url) as resp:
            return json.load(resp)

    def post(self, url: str, body: Any) -> Dict[str, Any]:
        with self._request("POST", url, body=body) as resp:
            return json.load(resp)

    def paged(self, url: str) -> Iterator[Dict[str, Any]]:
        """Yield each page of a JSON:API collection, following links.next."""
        while url:
            page = self.get(url)
            yield page
            url = (page.get("links", {}).get("next") or {}).get("href")

    # -- hubs / projects / folders ----------------------------------------- #

    def hubs(self) -> List[Dict[str, Any]]:
        return self.get(f"{APS_BASE}/project/v1/hubs").get("data", [])

    def projects(self, hub_id: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for page in self.paged(f"{APS_BASE}/project/v1/hubs/{hub_id}/projects?page[limit]=200"):
            out.extend(page.get("data", []))
        return out

    def top_folders(self, hub_id: str, project_id: str) -> List[Dict[str, Any]]:
        return self.get(f"{APS_BASE}/project/v1/hubs/{hub_id}/projects/{project_id}/topFolders").get("data", [])

    def folder_contents(self, project_id: str, folder_id: str) -> Iterator[Dict[str, Any]]:
        url = f"{APS_BASE}/data/v1/projects/{project_id}/folders/{folder_id}/contents?page[limit]=200"
        for page in self.paged(url):
            included = {inc["id"]: inc for inc in page.get("included", [])}
            for entry in page.get("data", []):
                if entry["type"] == "items":
                    tip_id = (entry.get("relationships", {}).get("tip", {}).get("data") or {}).get("id")
                    entry["_tip"] = included.get(tip_id) if tip_id else None
                yield entry

    def version(self, project_id: str, version_id: str) -> Dict[str, Any]:
        return self.get(f"{APS_BASE}/data/v1/projects/{project_id}/versions/{urllib.parse.quote(version_id, safe='')}").get("data", {})

    # -- export / download --------------------------------------------------- #

    def download_formats(self, project_id: str, version_id: str) -> List[str]:
        url = f"{APS_BASE}/data/v1/projects/{project_id}/versions/{urllib.parse.quote(version_id, safe='')}/downloadFormats"
        data = self.get(url).get("data", {})
        return [f["fileType"] for f in data.get("attributes", {}).get("formats", []) if f.get("fileType")]

    def existing_downloads(self, project_id: str, version_id: str, file_type: str) -> Optional[str]:
        """Return a storage URL for an already-generated export, if any."""
        url = (f"{APS_BASE}/data/v1/projects/{project_id}/versions/{urllib.parse.quote(version_id, safe='')}"
               f"/downloads?filter[format.fileType]={file_type}")
        try:
            data = self.get(url).get("data", [])
        except ApiError:
            return None
        for dl in data:
            href = self._storage_href(dl)
            if href:
                return href
        return None

    @staticmethod
    def _storage_href(download_obj: Dict[str, Any]) -> Optional[str]:
        storage = download_obj.get("relationships", {}).get("storage", {})
        href = (storage.get("meta", {}).get("link") or {}).get("href")
        if href:
            return href
        sid = (storage.get("data") or {}).get("id")
        return sid  # caller can resolve an OSS urn

    def export_version(self, project_id: str, version_id: str, file_type: str) -> str:
        """Ask the cloud to render a version into file_type; return a download URL/urn."""
        payload = {
            "jsonapi": {"version": "1.0"},
            "data": {
                "type": "downloads",
                "attributes": {"format": {"fileType": file_type}},
                "relationships": {"source": {"data": {"type": "versions", "id": version_id}}},
            },
        }
        created = self.post(f"{APS_BASE}/data/v1/projects/{project_id}/downloads", payload)
        data = created.get("data")
        if isinstance(data, list):
            data = data[0] if data else {}
        job_id = data.get("id")
        self_link = (data.get("links", {}).get("self") or {}).get("href")
        if not job_id:
            raise RuntimeError(f"Unexpected download response: {json.dumps(created)[:400]}")

        poll_url = self_link or f"{APS_BASE}/data/v1/projects/{project_id}/jobs/{urllib.parse.quote(job_id, safe='')}"
        deadline = time.time() + JOB_TIMEOUT_SECONDS
        while True:
            state = self.get(poll_url).get("data", {})
            if isinstance(state, list):
                state = state[0] if state else {}
            status = (state.get("attributes", {}).get("status") or "").lower()
            if state.get("type") == "downloads":
                href = self._storage_href(state)
                if href:
                    return href
            if status in ("finished", "complete", "completed", "success"):
                # Job done but no storage link on the job object; look it up.
                for url in (f"{APS_BASE}/data/v1/projects/{project_id}/downloads/{urllib.parse.quote(job_id, safe='')}",):
                    try:
                        href = self._storage_href(self.get(url).get("data", {}))
                        if href:
                            return href
                    except ApiError:
                        pass
                href = self.existing_downloads(project_id, version_id, file_type)
                if href:
                    return href
                raise RuntimeError(f"Export job finished but no download link found for {version_id} ({file_type})")
            if status in ("failed", "error"):
                raise RuntimeError(f"Export job failed for {version_id} ({file_type}): {json.dumps(state)[:300]}")
            if time.time() > deadline:
                raise RuntimeError(f"Timed out waiting for export of {version_id} ({file_type})")
            time.sleep(JOB_POLL_SECONDS)

    # -- fetching bytes ------------------------------------------------------ #

    _OSS_URN = re.compile(r"^urn:adsk\.objects:os\.object:([^/]+)/(.+)$")

    def resolve_storage(self, href_or_urn: str) -> str:
        """Turn an OSS object urn (or legacy OSS href) into a signed S3 URL."""
        m = self._OSS_URN.match(href_or_urn)
        if not m and "/oss/v2/buckets/" in href_or_urn:
            parts = href_or_urn.split("/oss/v2/buckets/", 1)[1].split("/objects/", 1)
            if len(parts) == 2:
                m = re.match(r"([^/]+)/(.+)", f"{parts[0]}/{parts[1]}")
        if not m:
            return href_or_urn  # already a plain (signed) URL
        bucket, obj = m.group(1), m.group(2)
        url = f"{APS_BASE}/oss/v2/buckets/{bucket}/objects/{urllib.parse.quote(obj, safe='')}/signeds3download"
        return self.get(url)["url"]

    def download_to(self, href_or_urn: str, dest: Path) -> int:
        url = self.resolve_storage(href_or_urn)
        needs_auth = url.startswith(APS_BASE)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".dl-", dir=dest.parent)
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            with self._request("GET", url, auth=needs_auth) as resp, tmp.open("wb") as out:
                shutil.copyfileobj(resp, out, length=1024 * 1024)
            size = tmp.stat().st_size
            os.replace(tmp, dest)
            return size
        finally:
            if tmp.exists():
                tmp.unlink()


# --------------------------------------------------------------------------- #
# Sync engine
# --------------------------------------------------------------------------- #

def item_kind(ext_type: str) -> str:
    t = (ext_type or "").lower()
    if "fusion360:design" in t:
        return "design"
    if "fusion360:drawing" in t:
        return "drawing"
    if "fusion360:cam" in t:
        return "cam"
    return "other"


def choose_formats(requested: List[str], available: List[str], kind: str) -> List[str]:
    """Map the user's format list (which may contain 'native') to real formats."""
    avail = [a.lower() for a in available]
    chosen: List[str] = []
    for want in requested:
        want = want.lower()
        if want == "native":
            for pref in NATIVE_PREFERENCE.get(kind, []):
                if pref in avail:
                    chosen.append(pref)
                    break
            else:
                if kind == "design" and not avail:
                    chosen.append("f3d")  # downloadFormats unavailable; f3d is always valid for designs
        elif want in avail or not avail:
            chosen.append(want)
    # de-dup, keep order
    seen = set()
    return [f for f in chosen if not (f in seen or seen.add(f))]


@dataclass
class SyncStats:
    scanned: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    bytes: int = 0


class Syncer:
    def __init__(self, cfg: Config, api: Aps, *, formats: List[str], dry_run: bool = False,
                 project_filter: Optional[List[str]] = None, hub_filter: Optional[List[str]] = None):
        self.cfg = cfg
        self.api = api
        self.formats = formats
        self.dry_run = dry_run
        self.project_filter = [p.lower() for p in (project_filter or cfg.projects)]
        self.hub_filter = [h.lower() for h in (hub_filter or cfg.hubs)]
        self.state_dir = cfg.root / STATE_DIRNAME
        self.manifest_path = self.state_dir / MANIFEST_NAME
        self.manifest: Dict[str, Any] = read_json(self.manifest_path, {"items": {}}) or {"items": {}}
        self.stats = SyncStats()

    # -- persistence ---------------------------------------------------------- #

    def save_manifest(self) -> None:
        if self.dry_run:
            return
        self.manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        write_json(self.manifest_path, self.manifest)

    # -- traversal ------------------------------------------------------------ #

    def run(self) -> SyncStats:
        log(f"Sync root: {self.cfg.root}   formats: {', '.join(self.formats)}   {'DRY RUN' if self.dry_run else ''}")
        for hub in self.api.hubs():
            hub_name = hub["attributes"]["name"]
            if self.hub_filter and hub_name.lower() not in self.hub_filter:
                continue
            log(f"Hub: {hub_name}")
            for project in self.api.projects(hub["id"]):
                proj_name = project["attributes"]["name"]
                if self.project_filter and proj_name.lower() not in self.project_filter:
                    continue
                log(f"  Project: {proj_name}")
                local_project = self.cfg.root / safe_name(hub_name) / safe_name(proj_name)
                try:
                    for top in self.api.top_folders(hub["id"], project["id"]):
                        self._walk_folder(project["id"], top, local_project / safe_name(top["attributes"]["name"]))
                except ApiError as exc:
                    log(f"  ! skipping project {proj_name}: {exc}", err=True)
                    self.stats.failed += 1
                self.save_manifest()
        self.save_manifest()
        s = self.stats
        log(f"Done. scanned={s.scanned} downloaded={s.downloaded} skipped={s.skipped} failed={s.failed} bytes={s.bytes:,}")
        return s

    def _walk_folder(self, project_id: str, folder: Dict[str, Any], local_dir: Path) -> None:
        for entry in self.api.folder_contents(project_id, folder["id"]):
            attrs = entry.get("attributes", {})
            if attrs.get("hidden"):
                continue
            if entry["type"] == "folders":
                self._walk_folder(project_id, entry, local_dir / safe_name(attrs.get("displayName") or attrs.get("name", "folder")))
            elif entry["type"] == "items":
                self._sync_item(project_id, entry, local_dir)

    # -- per-item ------------------------------------------------------------- #

    def _sync_item(self, project_id: str, item: Dict[str, Any], local_dir: Path) -> None:
        self.stats.scanned += 1
        attrs = item.get("attributes", {})
        tip = item.get("_tip")
        if not tip:
            try:
                tip_id = item["relationships"]["tip"]["data"]["id"]
                tip = self.api.version(project_id, tip_id)
            except (KeyError, ApiError):
                log(f"    ? no tip version for {attrs.get('displayName')}", err=True)
                self.stats.failed += 1
                return

        display = attrs.get("displayName") or tip["attributes"].get("displayName") or tip["attributes"].get("name") or item["id"]
        ext_type = (tip.get("attributes", {}).get("extension") or {}).get("type") or (attrs.get("extension") or {}).get("type", "")
        kind = item_kind(ext_type)
        version_id = tip["id"]
        version_no = tip.get("attributes", {}).get("versionNumber")
        storage = tip.get("relationships", {}).get("storage", {})
        storage_href = (storage.get("meta", {}).get("link") or {}).get("href") or (storage.get("data") or {}).get("id")

        record = self.manifest["items"].get(item["id"], {})
        rel_dir = local_dir.relative_to(self.cfg.root)
        base = safe_name(display)

        # Which files do we want for this item?
        wanted: Dict[str, Path] = {}  # fmt -> local path
        if kind == "other" and storage_href:
            # Uploaded file: download original bytes as-is, keep its own extension.
            name = tip["attributes"].get("name") or display
            wanted["raw"] = local_dir / safe_name(name)
        else:
            try:
                available = self.api.download_formats(project_id, version_id)
            except ApiError as exc:
                log(f"    ! cannot list export formats for {display}: {exc}", err=True)
                available = []
            fmts = choose_formats(self.formats, available, kind)
            if not fmts:
                log(f"    - {rel_dir}/{display}: no exportable format (available: {available or 'none'})")
                self.stats.skipped += 1
                return
            stem = base
            for ext in (".f3d", ".f3z"):
                if stem.lower().endswith(ext):
                    stem = stem[: -len(ext)]
            for fmt in fmts:
                wanted[fmt] = local_dir / f"{stem}.{fmt}"

        up_to_date = record.get("version_id") == version_id
        todo = {fmt: p for fmt, p in wanted.items()
                if not (up_to_date and p.exists() and record.get("files", {}).get(fmt) == str(p.relative_to(self.cfg.root)))}

        if not todo:
            self.stats.skipped += 1
            return

        label = f"{rel_dir}/{display} (v{version_no})"
        if self.dry_run:
            log(f"    would fetch {label} -> {', '.join(todo)}")
            return

        # Move the previous copies aside if the cloud version changed.
        if record and record.get("version_id") != version_id and self.cfg.keep_versions:
            for fmt, rel in record.get("files", {}).items():
                old = self.cfg.root / rel
                if old.exists():
                    keep = self.state_dir / VERSIONS_DIRNAME / rel_dir / f"{old.stem}.v{record.get('version_number', '?')}{old.suffix}"
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(old), str(keep))

        files = dict(record.get("files", {})) if up_to_date else {}
        for fmt, dest in todo.items():
            try:
                if fmt == "raw":
                    href = storage_href
                else:
                    href = self.api.existing_downloads(project_id, version_id, fmt) or \
                        self.api.export_version(project_id, version_id, fmt)
                size = self.api.download_to(href, dest)
                files[fmt] = str(dest.relative_to(self.cfg.root))
                self.stats.downloaded += 1
                self.stats.bytes += size
                log(f"    + {label} -> {dest.relative_to(self.cfg.root)} ({size:,} bytes)")
            except Exception as exc:  # keep going; one bad export shouldn't stop the run
                self.stats.failed += 1
                log(f"    ! {label} [{fmt}]: {exc}", err=True)

        if files:
            self.manifest["items"][item["id"]] = {
                "display_name": display,
                "kind": kind,
                "extension_type": ext_type,
                "project_id": project_id,
                "version_id": version_id,
                "version_number": version_no,
                "last_modified": tip.get("attributes", {}).get("lastModifiedTime"),
                "files": files,
                "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }


# --------------------------------------------------------------------------- #
# launchd integration (macOS background runs)
# --------------------------------------------------------------------------- #

LAUNCHD_LABEL = "com.fusion-sync.agent"


def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def install_launchd(cfg: Config, interval: int) -> None:
    log_dir = cfg.root / STATE_DIRNAME
    log_dir.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [sys.executable, str(Path(__file__).resolve()), "sync"],
        "StartInterval": interval,
        "RunAtLoad": True,
        "StandardOutPath": str(log_dir / "sync.log"),
        "StandardErrorPath": str(log_dir / "sync.err.log"),
        "EnvironmentVariables": {"FUSION_SYNC_CONFIG_DIR": str(CONFIG_DIR)},
    }
    path = launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        plistlib.dump(plist, fh)
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
    subprocess.run(["launchctl", "load", str(path)], check=True)
    log(f"Installed launchd agent {LAUNCHD_LABEL}: every {interval}s, logs in {log_dir}")


def uninstall_launchd() -> None:
    path = launchd_plist_path()
    if path.exists():
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        path.unlink()
        log(f"Removed {path}")
    else:
        log("No launchd agent installed")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def cmd_init(args: argparse.Namespace) -> None:
    cfg = Config(
        client_id=args.client_id,
        client_secret=args.client_secret,
        root=Path(args.root).expanduser().resolve(),
        callback_port=args.port,
        formats=[f.strip() for f in args.formats.split(",") if f.strip()],
    )
    cfg.root.mkdir(parents=True, exist_ok=True)
    cfg.save()
    log(f"Config written to {CONFIG_PATH}")
    log(f"Callback URL to register in your APS app: {cfg.redirect_uri}")
    log("Next: fusion_sync.py auth")


def cmd_auth(args: argparse.Namespace) -> None:
    cfg = Config.load()
    Auth(cfg).login(open_browser=not args.no_browser)


def cmd_hubs(_args: argparse.Namespace) -> None:
    cfg = Config.load()
    api = Aps(Auth(cfg))
    for hub in api.hubs():
        print(f"{hub['attributes']['name']}  [{hub['id']}]  {hub['attributes'].get('extension', {}).get('type', '')}")
        for project in api.projects(hub["id"]):
            print(f"    {project['attributes']['name']}  [{project['id']}]")


def _formats_from_args(cfg: Config, args: argparse.Namespace) -> List[str]:
    if getattr(args, "formats", None):
        return [f.strip() for f in args.formats.split(",") if f.strip()]
    return cfg.formats


def cmd_sync(args: argparse.Namespace) -> None:
    cfg = Config.load()
    api = Aps(Auth(cfg))
    syncer = Syncer(cfg, api, formats=_formats_from_args(cfg, args), dry_run=args.dry_run,
                    project_filter=args.project, hub_filter=args.hub)
    stats = syncer.run()
    sys.exit(1 if stats.failed and not stats.downloaded else 0)


def cmd_watch(args: argparse.Namespace) -> None:
    cfg = Config.load()
    api = Aps(Auth(cfg))
    while True:
        try:
            Syncer(cfg, api, formats=_formats_from_args(cfg, args), project_filter=args.project, hub_filter=args.hub).run()
        except Exception as exc:
            log(f"sync pass failed: {exc}", err=True)
        log(f"Sleeping {args.interval}s")
        time.sleep(args.interval)


def cmd_status(_args: argparse.Namespace) -> None:
    cfg = Config.load()
    manifest = read_json(cfg.root / STATE_DIRNAME / MANIFEST_NAME, {"items": {}})
    items = manifest.get("items", {})
    print(f"root: {cfg.root}")
    print(f"last sync: {manifest.get('updated_at', 'never')}")
    print(f"items mirrored: {len(items)}")
    by_kind: Dict[str, int] = {}
    for rec in items.values():
        by_kind[rec.get("kind", "?")] = by_kind.get(rec.get("kind", "?"), 0) + 1
    for kind, n in sorted(by_kind.items()):
        print(f"  {kind}: {n}")


def cmd_install_launchd(args: argparse.Namespace) -> None:
    install_launchd(Config.load(), args.interval)


def cmd_uninstall_launchd(_args: argparse.Namespace) -> None:
    uninstall_launchd()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fusion_sync.py", description="Mirror Fusion 360 cloud designs to a local folder.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="write config")
    s.add_argument("--client-id", required=True, help="APS app client id")
    s.add_argument("--client-secret", default=None, help="only for 'Traditional Web App' APS apps; PKCE apps need none")
    s.add_argument("--root", required=True, help="local folder to mirror into")
    s.add_argument("--port", type=int, default=DEFAULT_CALLBACK_PORT, help="localhost port for the OAuth callback")
    s.add_argument("--formats", default="native", help="comma list: native,f3d,f3z,step,stl,iges,sat,obj,fbx,dwg,pdf,...")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("auth", help="sign in to Autodesk (opens browser once)")
    s.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser")
    s.set_defaults(func=cmd_auth)

    s = sub.add_parser("hubs", help="list hubs and projects you can see")
    s.set_defaults(func=cmd_hubs)

    for name, fn in (("sync", cmd_sync), ("watch", cmd_watch)):
        s = sub.add_parser(name, help="run one sync pass" if name == "sync" else "sync forever on an interval")
        s.add_argument("--formats", help="override configured formats, e.g. native,step")
        s.add_argument("--project", action="append", help="only this project name (repeatable)")
        s.add_argument("--hub", action="append", help="only this hub name (repeatable)")
        if name == "sync":
            s.add_argument("--dry-run", action="store_true", help="show what would be fetched")
        else:
            s.add_argument("--interval", type=int, default=900, help="seconds between passes")
        s.set_defaults(func=fn)

    s = sub.add_parser("status", help="summarize the local mirror")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("install-launchd", help="run 'sync' in the background on macOS")
    s.add_argument("--interval", type=int, default=900)
    s.set_defaults(func=cmd_install_launchd)

    s = sub.add_parser("uninstall-launchd", help="remove the background agent")
    s.set_defaults(func=cmd_uninstall_launchd)
    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)
    except ApiError as exc:
        log(str(exc), err=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
