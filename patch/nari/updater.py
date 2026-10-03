from __future__ import annotations
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
import requests
from .config import ROOT, SETTINGS_FILE

APP_NAME = "NARI"
APP_VERSION = "5.4.0"
OFFICIAL_REPO = "zilafeindert/NARI"
GITHUB_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 120
PRESERVE_NAMES = {"data","voices","models",".venv",".env"}

def _normalize_version(value: str) -> tuple[int,...]:
    raw=str(value or "").strip().lstrip("vV").split("+")[0]
    nums=[]
    for part in raw.split(".")[:4]:
        digits="".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits or 0))
    while len(nums)<3: nums.append(0)
    return tuple(nums)

def _is_newer(latest: str, current: str = APP_VERSION) -> bool:
    return _normalize_version(latest) > _normalize_version(current)

def _validate_repo(repo: str) -> tuple[str,str]:
    value=(repo or OFFICIAL_REPO).strip().replace("https://github.com/","").strip("/")
    parts=value.split("/")
    if len(parts)!=2 or not all(parts): raise ValueError("Repositorio GitHub inválido.")
    return parts[0],parts[1]

def _github_latest(repo: str) -> dict:
    owner,name=_validate_repo(repo)
    headers={"Accept":"application/vnd.github+json","User-Agent":f"NARI-Updater/{APP_VERSION}"}
    response=requests.get(f"https://api.github.com/repos/{owner}/{name}/releases/latest",headers=headers,timeout=GITHUB_TIMEOUT)
    if response.status_code==404:
        response=requests.get(f"https://api.github.com/repos/{owner}/{name}/releases?per_page=1",headers=headers,timeout=GITHUB_TIMEOUT)
        response.raise_for_status()
        payload=response.json()
        data=payload[0] if payload else {}
    else:
        response.raise_for_status()
        data=response.json()
    tag=str(data.get("tag_name","")).strip()
    version=tag.lstrip("vV") or APP_VERSION
    zip_url=""
    for asset in data.get("assets") or []:
        if str(asset.get("name","")).lower().endswith(".zip"):
            zip_url=str(asset.get("browser_download_url","")); break
    return {"ok":True,"version":version,"zip_url":zip_url,
            "notes":str(data.get("body",""))[:2000],
            "release_url":str(data.get("html_url","")),
            "repo":f"{owner}/{name}"}

def check(url: str="", repo: str="") -> dict:
    return _github_latest(repo or OFFICIAL_REPO)

def apply(zip_url: str, version: str) -> str:
    if not zip_url: raise ValueError("La Release no contiene un ZIP.")
    tmp=Path(tempfile.mkdtemp(prefix="nari_update_"))
    archive=tmp/"update.zip"; extract=tmp/"extract"; backup=tmp/"backup"
    try:
        with requests.get(zip_url,stream=True,timeout=DOWNLOAD_TIMEOUT,headers={"User-Agent":f"NARI-Updater/{APP_VERSION}"}) as r:
            r.raise_for_status()
            with archive.open("wb") as f:
                for chunk in r.iter_content(1024*1024):
                    if chunk: f.write(chunk)
        with zipfile.ZipFile(archive) as zf:
            bad=zf.testzip()
            if bad: raise RuntimeError("ZIP dañado: "+bad)
            zf.extractall(extract)
        source=extract
        dirs=[p for p in extract.iterdir() if p.is_dir()]
        files=[p for p in extract.iterdir() if p.is_file()]
        if len(dirs)==1 and not files: source=dirs[0]
        backup.mkdir(parents=True,exist_ok=True)
        changed=[]
        try:
            for item in source.rglob("*"):
                if not item.is_file(): continue
                rel=item.relative_to(source)
                if rel.parts and rel.parts[0] in PRESERVE_NAMES: continue
                dest=ROOT/rel; dest.parent.mkdir(parents=True,exist_ok=True)
                if dest.exists() and dest.is_file():
                    b=backup/rel; b.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(dest,b); changed.append((dest,b))
                elif dest.exists() and dest.is_dir(): shutil.rmtree(dest)
                shutil.copy2(item,dest)
        except Exception:
            for dest,b in reversed(changed):
                if dest.exists() and dest.is_file(): dest.unlink()
                if b.exists(): shutil.copy2(b,dest)
            raise
        return f"NARI actualizado a {version}. Se conservaron memoria, modelos, voces, .venv y .env."
    finally:
        shutil.rmtree(tmp,ignore_errors=True)

def load_settings() -> dict:
    try: return json.loads(SETTINGS_FILE.read_text(encoding="utf-8")) if SETTINGS_FILE.exists() else {}
    except Exception: return {}
