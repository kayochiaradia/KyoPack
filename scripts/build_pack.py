#!/usr/bin/env python3
"""
KyoPack build script.

Consulta a release mais recente de cada componente listado em
components.yaml, baixa e posiciona os arquivos numa pasta build/, monta um
zip versionado em dist/ e gera um changelog no estilo:

    [Changelog]

    Atualizações do Pacote.

    - Componente vX.Y.Z

    [Problemas Conhecidos]
    ...

Uso:
    python scripts/build_pack.py [--force]

--force  ignora o cache de versões e refaz o pacote mesmo sem mudanças.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPONENTS_FILE = ROOT / "components.yaml"
VERSIONS_FILE = ROOT / "state" / "versions.json"
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"

GITHUB_API = "https://api.github.com"
PACK_NAME = "KyoPack"
USER_AGENT = "KyoPack-builder (+https://github.com/kayochiaradia/KyoPack)"
REQUEST_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 120
MAX_RETRIES = 3
RETRY_BACKOFF = (2, 5, 10)

SELF_REPO = os.environ.get("GITHUB_REPOSITORY", "kayochiaradia/KyoPack")
KNOWN_ISSUES_LABEL = "known-issue"


def _with_retries(url: str, req: Request, timeout: int) -> bytes:
    last_err: Exception | None = None
    for attempt, delay in enumerate((0, *RETRY_BACKOFF), start=1):
        if delay:
            time.sleep(delay)
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except HTTPError as e:
            if e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0":
                raise RuntimeError(
                    f"Rate limit da API do GitHub atingido ao consultar {url}. "
                    "Defina GITHUB_TOKEN no ambiente para um limite maior."
                ) from e
            if e.code in (429, 500, 502, 503, 504) and attempt <= MAX_RETRIES:
                last_err = e
                continue
            raise RuntimeError(f"Falha ao consultar {url}: {e.code} {e.reason}") from e
        except URLError as e:
            if attempt <= MAX_RETRIES:
                last_err = e
                continue
            raise RuntimeError(f"Falha de rede ao consultar {url}: {e.reason}") from e
    raise RuntimeError(f"Falha ao consultar {url} após {MAX_RETRIES} tentativas") from last_err


def gh_request(path: str) -> dict | list:
    url = f"{GITHUB_API}{path}"
    req = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return json.loads(_with_retries(url, req, REQUEST_TIMEOUT).decode())


def download(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return _with_retries(url, req, DOWNLOAD_TIMEOUT)


def known_issues_section(repo: str) -> str:
    header = "[Problemas Conhecidos]"
    fallback = (
        f"{header}\n\n"
        "Nenhum problema conhecido no momento.\n"
        "Encontrou algo? Abra uma issue no repositório do KyoPack."
    )
    try:
        issues = gh_request(
            f"/repos/{repo}/issues?state=open&labels={KNOWN_ISSUES_LABEL}&per_page=100"
        )
    except RuntimeError as e:
        print(f"Aviso: não deu pra consultar issues conhecidas ({e}). Usando texto padrão.")
        return fallback

    # a API de issues também devolve pull requests; eles não contam aqui.
    issues = [i for i in issues if "pull_request" not in i]
    if not issues:
        return fallback

    lines = [header, ""]
    lines += [f"- {issue['title']} ({issue['html_url']})" for issue in issues]
    lines += ["", "Encontrou outro problema? Abra uma issue no repositório do KyoPack."]
    return "\n".join(lines)


def pick_asset(assets: list[dict], pattern: str) -> dict:
    regex = re.compile(pattern)
    for asset in assets:
        if regex.search(asset["name"]):
            return asset
    names = ", ".join(a["name"] for a in assets)
    raise RuntimeError(f"Nenhum asset bateu com '{pattern}'. Disponíveis: {names}")


def place_zip_merge(data: bytes, dest_root: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest_root)


def place_zip_to(data: bytes, dest_root: Path, sub: str) -> None:
    target = dest_root / sub
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(target)


def place_copy_to(data: bytes, dest_root: Path, sub: str, filename: str) -> None:
    target = dest_root / sub
    target.mkdir(parents=True, exist_ok=True)
    (target / filename).write_bytes(data)


def place_asset(asset_spec: dict, asset: dict, data: bytes, build_dir: Path) -> None:
    place = asset_spec["place"]
    if place == "zip-merge":
        place_zip_merge(data, build_dir)
    elif place == "zip-to":
        place_zip_to(data, build_dir, asset_spec["dest"])
    elif place == "copy-to":
        filename = asset_spec.get("rename", asset["name"])
        place_copy_to(data, build_dir, asset_spec["dest"], filename)
    else:
        raise RuntimeError(f"'place' desconhecido: {place}")


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_versions(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_versions(path: Path, versions: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(versions, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def next_pack_version(dist_dir: Path) -> str:
    today = date.today().strftime("%Y.%m.%d")
    existing = sorted(dist_dir.glob(f"{PACK_NAME}-{today}-*.zip"))
    n = len(existing) + 1
    return f"{today}-{n}"


def zip_build_dir(build_dir: Path, out_path: Path) -> None:
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in build_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(build_dir))


def write_checksum(zip_path: Path) -> Path:
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    checksum_path = zip_path.with_suffix(zip_path.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    return checksum_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="reconstrói mesmo sem mudanças")
    args = parser.parse_args()

    config = load_yaml(COMPONENTS_FILE)
    old_versions = load_versions(VERSIONS_FILE)
    new_versions = dict(old_versions)

    updates: list[tuple[str, str, str]] = []  # (nome, versao_antiga, versao_nova)

    if BUILD_DIR.exists():
        import shutil
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True)
    DIST_DIR.mkdir(exist_ok=True)

    for comp in config["components"]:
        name = comp["name"]
        repo = comp["repo"]
        print(f"==> {name} ({repo})")

        release = gh_request(f"/repos/{repo}/releases/latest")
        tag = release["tag_name"]
        version = tag[1:] if comp.get("strip_v") and tag.lower().startswith("v") else tag
        print(f"    versão: {version}")

        for asset_spec in comp["assets"]:
            asset = pick_asset(release["assets"], asset_spec["regex"])
            print(f"    asset: {asset['name']} -> {asset_spec['place']}")
            data = download(asset["browser_download_url"])
            place_asset(asset_spec, asset, data, BUILD_DIR)

        if old_versions.get(name) != version:
            updates.append((name, old_versions.get(name, "novo"), version))
        new_versions[name] = version

    if not updates and not args.force:
        print("Nada mudou desde a última execução. Nenhum pacote gerado.")
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a", encoding="utf-8") as f:
                f.write("updated=false\n")
        return 0

    pack_version = next_pack_version(DIST_DIR)
    zip_path = DIST_DIR / f"{PACK_NAME}-{pack_version}.zip"
    zip_build_dir(BUILD_DIR, zip_path)
    checksum_path = write_checksum(zip_path)
    print(f"Pacote gerado: {zip_path}")
    print(f"Checksum: {checksum_path}")

    changelog_lines = ["[Changelog]", "", "Atualizações do Pacote.", ""]
    if updates:
        for name, old, new in updates:
            changelog_lines.append(f"- {name} {new}.")
    else:
        changelog_lines.append("- Rebuild forçado, sem mudanças de versão.")
    changelog_lines += ["", known_issues_section(SELF_REPO)]
    changelog = "\n".join(changelog_lines)

    changelog_path = DIST_DIR / "CHANGELOG_latest.md"
    changelog_path.write_text(changelog, encoding="utf-8")
    print("\n" + changelog)

    save_versions(VERSIONS_FILE, new_versions)

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write("updated=true\n")
            f.write(f"pack_version={pack_version}\n")
            f.write(f"zip_path={zip_path}\n")
            f.write(f"checksum_path={checksum_path}\n")
            f.write(f"changelog_path={changelog_path}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
