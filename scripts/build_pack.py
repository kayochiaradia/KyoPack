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
import io
import json
import os
import re
import sys
import zipfile
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPONENTS_FILE = ROOT / "components.yaml"
VERSIONS_FILE = ROOT / "state" / "versions.json"
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"

GITHUB_API = "https://api.github.com"
PACK_NAME = "KyoPack"

KNOWN_ISSUES = (
    "[Problemas Conhecidos]\n\n"
    "Nenhum problema conhecido no momento.\n"
    "Encontrou algo? Abra uma issue no repositório do KyoPack."
)


def gh_request(path: str) -> dict:
    url = f"{GITHUB_API}{path}"
    req = Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        raise RuntimeError(f"Falha ao consultar {url}: {e.code} {e.reason}") from e


def download(url: str) -> bytes:
    req = Request(url)
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urlopen(req) as resp:
        return resp.read()


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
    print(f"Pacote gerado: {zip_path}")

    changelog_lines = ["[Changelog]", "", "Atualizações do Pacote.", ""]
    if updates:
        for name, old, new in updates:
            changelog_lines.append(f"- {name} {new}.")
    else:
        changelog_lines.append("- Rebuild forçado, sem mudanças de versão.")
    changelog_lines += ["", KNOWN_ISSUES]
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
            f.write(f"changelog_path={changelog_path}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
