"""Load the current user's WorkBuddy session without exposing its token.

The sym-v1 helper is adapted from 88lin/workbuddy-auto-signin (MIT),
copyright (c) 2026 88lin. See ../LICENSE and ../references/sources.md.
"""

from __future__ import annotations

import base64
import json
import os
import plistlib
import re
import string
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_BASE = "https://copilot.tencent.com"
ALLOWED_HOSTS = {"copilot.tencent.com", "www.codebuddy.cn", "www.workbuddy.cn"}
AUTH_BASENAME = Path("CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info")
CLI_AUTH_BASENAME = Path("CodeBuddyExtension/Data/Public/auth/Tencent-Cloud.coding-copilot.info")
TOKEN_LIMIT = 32768
HELPER_TIMEOUT = 12
RUNTIME_CACHE_NAME = ".runtime-cache"
SCAN_MAX_DIRS = 1200
SCAN_SKIP_DIRS = {
    "users", "windows", "programdata", "appdata", "perflogs", "recovery", "msocache",
    "system volume information", "$recycle.bin", "program files", "program files (x86)",
    "node_modules", ".git", "venv", ".venv", "__pycache__",
}
REGISTRY_UNINSTALL_KEYS = (
    ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
)
REGISTRY_APP_PATH_KEY = ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WorkBuddy.exe")


class CredentialError(Exception):
    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class Session:
    token: str
    uid: str
    domain: str | None
    enterprise_id: str | None
    api_base: str
    credential_format: str


AUTH_HELPER_JS = r"""
'use strict';
const crypto = require('crypto');
const fail = reason => { throw {reason}; };
const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
function b64(value, length) {
  if (typeof value !== 'string' || value.length > 65536) fail('INVALID_FORMAT');
  const out = Buffer.from(value, 'base64');
  if (out.toString('base64') !== value || (length !== undefined && out.length !== length)) fail('INVALID_FORMAT');
  return out;
}
function utf8(bytes) {
  const text = bytes.toString('utf8');
  if (!Buffer.from(text, 'utf8').equals(bytes)) fail('INVALID_FORMAT');
  return text;
}
function envelope(value) {
  if (!object(value) || Object.keys(value).sort().join(',') !== '$wbEncrypted,envelope' || value.$wbEncrypted !== 1)
    fail('UNSUPPORTED_ENVELOPE');
  let e;
  try { e = JSON.parse(utf8(b64(value.envelope))); } catch (_) { fail('INVALID_FORMAT'); }
  if (!object(e) || e.suite !== 1 || Object.keys(e).sort().join(',') !== 'authTag,ciphertext,keyId,nonce,suite')
    fail(e && Number.isInteger(e.suite) ? 'UNSUPPORTED_ENVELOPE' : 'INVALID_FORMAT');
  if (typeof e.keyId !== 'string' || !/^[0-9a-f]{16}$/.test(e.keyId)) fail('INVALID_FORMAT');
  return {keyId: e.keyId, nonce: b64(e.nonce, 12), tag: b64(e.authTag, 16), ciphertext: b64(e.ciphertext)};
}
function storage() {
  try {
    const s = process._linkedBinding('electron_browser_workbuddy_storage');
    if (typeof s.loggerGet !== 'function') fail('RUNTIME_UNAVAILABLE');
    return s;
  } catch (_) { fail('RUNTIME_UNAVAILABLE'); }
}
function decrypt(e) {
  let payload, key, plaintext;
  try {
    try { payload = JSON.parse(storage().loggerGet()); } catch (_) { fail('RUNTIME_UNAVAILABLE'); }
    if (!object(payload) || payload.version !== 1) fail('RUNTIME_UNAVAILABLE');
    let secret;
    try { secret = b64(payload.atRestSecretKey, 32); } catch (_) { fail('RUNTIME_UNAVAILABLE'); }
    const empty = secret.every(x => x === 0); secret.fill(0);
    if (empty) fail('RUNTIME_UNAVAILABLE');
    key = crypto.createHash('sha256').update(payload.atRestSecretKey, 'utf8').digest();
    payload = null;
    if (crypto.createHash('sha256').update(key).digest('hex').slice(0, 16) !== e.keyId) fail('KEY_MISMATCH');
    const lp = s => { const b = Buffer.from(s, 'utf8'); const n = Buffer.alloc(4); n.writeUInt32BE(b.length); return Buffer.concat([n, b]); };
    const aad = Buffer.concat([Buffer.from('WB-AAD\0', 'ascii'), Buffer.from([1]), lp('WBEV1'), lp('sym-v1'),
      Buffer.from([0,0,0,1]), lp(e.keyId), Buffer.from([2,0,0])]);
    try {
      const c = crypto.createDecipheriv('aes-256-gcm', key, e.nonce, {authTagLength: 16});
      c.setAAD(aad); c.setAuthTag(e.tag);
      plaintext = Buffer.concat([c.update(e.ciphertext), c.final()]);
    } catch (_) { fail('DECRYPT_FAILED'); }
    const token = utf8(plaintext);
    if (!token.length || token.length > 32768 || !/^[A-Za-z0-9._~+\/-]+=*$/.test(token)) fail('INVALID_FORMAT');
    return token;
  } finally {
    if (key) key.fill(0);
    if (plaintext) plaintext.fill(0);
  }
}
function reply(value) { process.stdout.write(JSON.stringify({version:1, ...value}), () => process.exit(value.ok ? 0 : 1)); }
let chunks = [], size = 0;
process.stdin.on('data', c => { size += c.length; if (size > 65536) reply({ok:false,reason:'INVALID_FORMAT'}); else chunks.push(c); });
process.stdin.on('end', () => {
  try {
    const req = JSON.parse(utf8(Buffer.concat(chunks))); chunks = [];
    if (!object(req) || req.version !== 1) fail('HELPER_PROTOCOL');
    if (req.operation === 'probe') { storage(); reply({ok:true,electron:process.versions.electron || 'unknown'}); }
    else if (req.operation === 'decrypt') reply({ok:true,accessToken:decrypt(envelope(req.value))});
    else fail('HELPER_PROTOCOL');
  } catch (e) {
    const allowed = ['INVALID_FORMAT','UNSUPPORTED_ENVELOPE','RUNTIME_UNAVAILABLE','KEY_MISMATCH','DECRYPT_FAILED','HELPER_PROTOCOL'];
    reply({ok:false,reason:allowed.includes(e.reason) ? e.reason : 'HELPER_PROTOCOL'});
  }
});
"""


def _token_valid(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= TOKEN_LIMIT and re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", value) is not None


def _candidate_paths() -> list[Path]:
    override = os.environ.get("WORKBUDDY_AUTH_FILE")
    if override:
        return [Path(override).expanduser()]
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
    xdg = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    return [
        local / AUTH_BASENAME,
        home / "Library/Application Support" / AUTH_BASENAME,
        xdg / CLI_AUTH_BASENAME,
        home / ".config" / AUTH_BASENAME,
        home / ".workbuddy/auth/workbuddy-desktop.info",
    ]


def find_auth_file() -> Path:
    for path in _candidate_paths():
        if path.is_file():
            return path
    raise CredentialError("NO_AUTH_FILE", "未找到 WorkBuddy 本地登录态；请先打开客户端并登录")


def _read_json(path: Path) -> dict[str, Any]:
    for attempt in range(3):
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except PermissionError:
            if attempt == 2:
                raise CredentialError("AUTH_LOCKED", "登录态文件暂时被客户端占用，请稍后重试") from None
            import time
            time.sleep(1)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            raise CredentialError("INVALID_AUTH_FILE", "WorkBuddy 登录态文件不是有效 JSON") from None
    raise AssertionError("unreachable")


def _token_format(value: Any) -> str:
    if _token_valid(value):
        return "plaintext"
    if not isinstance(value, dict) or set(value) != {"$wbEncrypted", "envelope"} or value.get("$wbEncrypted") != 1:
        raise CredentialError("UNSUPPORTED_ENVELOPE", "不支持当前 WorkBuddy 登录态格式，请更新本 Skill")
    encoded = value.get("envelope")
    try:
        raw = base64.b64decode(encoded, validate=True)
        parsed = json.loads(raw.decode("utf-8"))
        if set(parsed) != {"suite", "keyId", "nonce", "authTag", "ciphertext"} or parsed.get("suite") != 1:
            raise ValueError
        if not re.fullmatch(r"[0-9a-f]{16}", parsed.get("keyId", "")):
            raise ValueError
        for key, length in (("nonce", 12), ("authTag", 16), ("ciphertext", None)):
            data = base64.b64decode(parsed[key], validate=True)
            if length is not None and len(data) != length:
                raise ValueError
    except (TypeError, ValueError, KeyError, UnicodeError, json.JSONDecodeError):
        raise CredentialError("INVALID_ENVELOPE", "WorkBuddy 加密登录态格式损坏或无法识别") from None
    return "sym-v1"


def _mac_runtime(bundle: Path) -> Path | None:
    try:
        with (bundle / "Contents/Info.plist").open("rb") as handle:
            name = plistlib.load(handle).get("CFBundleExecutable")
        if isinstance(name, str) and name not in {"", ".", ".."} and "/" not in name and "\\" not in name:
            return bundle / "Contents/MacOS" / name
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def _cache_file() -> Path:
    return Path(__file__).resolve().parent.parent / RUNTIME_CACHE_NAME


def _read_runtime_cache() -> Path | None:
    try:
        value = _cache_file().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    if not value or len(value) > 4096:
        return None
    return _existing_runtime(Path(value))


def _write_runtime_cache(path: Path) -> None:
    try:
        _cache_file().write_text(str(path), encoding="utf-8")
    except OSError:
        pass


def _existing_runtime(path: Path) -> Path | None:
    """Accept only a real WorkBuddy install: the binary plus its Electron resources."""
    try:
        if not path.is_file() or path.name.lower() != "workbuddy.exe":
            return None
        if not (path.parent / "resources").is_dir():
            return None
        return path.resolve()
    except OSError:
        return None


def _clean_runtime(values: Iterable[Any]) -> Path | None:
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        text = value.strip().strip('"')
        if "," in text and text.rsplit(",", 1)[-1].strip().isdigit():
            text = text.rsplit(",", 1)[0].strip().strip('"')
        candidate = Path(text)
        if candidate.name.lower().startswith("uninstall"):
            candidate = candidate.parent
        if candidate.name.lower() != "workbuddy.exe":
            candidate = candidate / "WorkBuddy.exe"
        found = _existing_runtime(candidate)
        if found:
            return found
    return None


def _registry_runtime() -> Path | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    hives = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    found: Path | None = None
    try:
        handle = winreg.OpenKey(hives[REGISTRY_APP_PATH_KEY[0]], REGISTRY_APP_PATH_KEY[1])
    except OSError:
        handle = None
    if handle is not None:
        try:
            found = _clean_runtime([winreg.QueryValue(handle, "")])
        except OSError:
            found = None
        winreg.CloseKey(handle)
    if found:
        return found
    for hive, base in REGISTRY_UNINSTALL_KEYS:
        try:
            handle = winreg.OpenKey(hives[hive], base)
        except OSError:
            continue
        candidates: list[str] = []
        index = 0
        while index < 300:
            try:
                name = winreg.EnumKey(handle, index)
            except OSError:
                break
            index += 1
            if "workbuddy" not in name.lower() and "codebuddy" not in name.lower():
                continue
            try:
                sub = winreg.OpenKey(handle, name)
            except OSError:
                continue
            for value_name in ("InstallLocation", "DisplayIcon", "UninstallString"):
                try:
                    candidates.append(winreg.QueryValueEx(sub, value_name)[0])
                except OSError:
                    pass
            winreg.CloseKey(sub)
        winreg.CloseKey(handle)
        found = _clean_runtime(candidates)
        if found:
            return found
    return None


def _scan_runtime() -> Path | None:
    """Bounded, non-recursive-ish search of fixed disks for a portable install."""
    if sys.platform != "win32":
        return None
    pending: list[Path] = []
    for letter in string.ascii_uppercase:
        root = Path(letter + ":\\")
        try:
            if root.is_dir():
                pending.append(root)
        except OSError:
            continue
    visited = 0
    while pending:
        following: list[Path] = []
        for directory in pending:
            visited += 1
            if visited > SCAN_MAX_DIRS:
                return None
            found = _existing_runtime(directory / "WorkBuddy" / "WorkBuddy.exe")
            if found:
                return found
            try:
                children = [child for child in directory.iterdir() if child.is_dir()]
            except OSError:
                continue
            for child in children:
                if visited + len(following) > SCAN_MAX_DIRS:
                    break
                if child.name.lower() in SCAN_SKIP_DIRS or child.name.startswith("."):
                    continue
                following.append(child)
        pending = following
    return None


def find_runtime() -> Path:
    """Locate the WorkBuddy executable that can decrypt the local session.

    Order matters: an explicit override wins, then the path remembered from a
    previous successful run, then the running process. Only if all of those miss
    do we fall back to registry entries and a bounded disk search, because those
    could otherwise be influenced by an unrelated binary of the same name.
    """
    override = os.environ.get("WORKBUDDY_EXE")
    if override:
        path = Path(override).expanduser().resolve()
        if path.is_file():
            return path
        raise CredentialError("INVALID_RUNTIME_PATH", "WORKBUDDY_EXE 不是可用的 WorkBuddy 可执行文件")
    cached = _read_runtime_cache()
    if cached:
        return cached
    home = Path.home()
    candidates: list[Path | None] = []
    if sys.platform == "win32":
        # Portable/store installs may live outside the standard directories. If the
        # client is running, Windows can report the exact executable without a disk scan.
        candidates.append(_running_windows_runtime())
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        candidates.append(local / "Programs/WorkBuddy/WorkBuddy.exe")
        for key in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(key):
                candidates.append(Path(os.environ[key]) / "WorkBuddy/WorkBuddy.exe")
    elif sys.platform == "darwin":
        candidates.extend(_mac_runtime(root / "WorkBuddy.app") for root in (Path("/Applications"), home / "Applications"))
    for candidate in candidates:
        if candidate and candidate.is_file():
            resolved = candidate.resolve()
            if _existing_runtime(resolved):
                _write_runtime_cache(resolved)
            return resolved
    found = _registry_runtime() or _scan_runtime()
    if found:
        _write_runtime_cache(found)
        return found
    raise CredentialError("RUNTIME_NOT_FOUND", "未找到 WorkBuddy 客户端；可用 WORKBUDDY_EXE 指定路径")


def _running_windows_runtime() -> Path | None:
    try:
        command = (
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
            "Get-Process -Name WorkBuddy -ErrorAction SilentlyContinue | "
            "Select-Object -First 1 -ExpandProperty Path"
        )
        found = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.decode("utf-8", "ignore").strip()
        return Path(found) if found else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _run_helper(request: dict[str, Any]) -> dict[str, Any]:
    runtime = find_runtime()
    payload = json.dumps({**request, "version": 1}, ensure_ascii=True).encode("ascii")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("NODE_", "ELECTRON_", "WORKBUDDY_"))}
    env["ELECTRON_RUN_AS_NODE"] = "1"
    try:
        result = subprocess.run(
            [str(runtime), "-e", AUTH_HELPER_JS],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=HELPER_TIMEOUT,
            check=False,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        raise CredentialError("HELPER_TIMEOUT", "WorkBuddy 凭据处理超时") from None
    except OSError:
        raise CredentialError("RUNTIME_UNAVAILABLE", "无法启动 WorkBuddy 本地凭据运行时") from None
    if len(result.stdout) > 65536:
        raise CredentialError("HELPER_PROTOCOL", "WorkBuddy 凭据运行时返回异常")
    try:
        reply = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise CredentialError("HELPER_PROTOCOL", "WorkBuddy 凭据运行时返回异常") from None
    if not isinstance(reply, dict) or reply.get("version") != 1 or reply.get("ok") is not True or result.returncode != 0:
        reason = reply.get("reason") if isinstance(reply, dict) else "HELPER_PROTOCOL"
        messages = {
            "KEY_MISMATCH": "登录态与本机 WorkBuddy 运行时不匹配",
            "DECRYPT_FAILED": "WorkBuddy 登录态解密认证失败",
            "RUNTIME_UNAVAILABLE": "当前 WorkBuddy 运行时不支持登录态解密",
            "UNSUPPORTED_ENVELOPE": "不支持当前 WorkBuddy 加密登录态格式",
        }
        raise CredentialError(str(reason), messages.get(str(reason), "WorkBuddy 凭据运行时返回异常"))
    return reply


def _api_base(auth: dict[str, Any]) -> str:
    candidates = [auth.get("endpoint"), auth.get("domain")]
    for value in candidates:
        if not isinstance(value, str) or not value:
            continue
        candidate = value if "://" in value else "https://" + value
        try:
            parsed = urllib.parse.urlsplit(candidate)
            if parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS and not parsed.username and not parsed.password:
                return "https://" + parsed.hostname
        except ValueError:
            continue
    return DEFAULT_BASE


def inspect_auth() -> dict[str, Any]:
    raw = _read_json(find_auth_file())
    auth, account = raw.get("auth"), raw.get("account")
    if not isinstance(auth, dict) or not isinstance(account, dict) or not account.get("uid"):
        raise CredentialError("NO_SESSION", "本地登录态缺少当前账号信息；请重新登录 WorkBuddy")
    kind = _token_format(auth.get("accessToken"))
    result: dict[str, Any] = {"status": "ready", "credential_format": kind, "online_checked": False}
    if kind == "sym-v1":
        probe = _run_helper({"operation": "probe"})
        result["runtime_ready"] = True
        result["electron_version"] = probe.get("electron")
    return result


def load_session() -> Session:
    raw = _read_json(find_auth_file())
    auth, account = raw.get("auth"), raw.get("account")
    if not isinstance(auth, dict) or not isinstance(account, dict):
        raise CredentialError("NO_SESSION", "本地未找到有效登录会话；请重新登录 WorkBuddy")
    uid = account.get("uid")
    printable = re.compile(r"[\x21-\x7e]{1,2048}")
    if not isinstance(uid, str) or printable.fullmatch(uid) is None:
        raise CredentialError("NO_SESSION", "本地登录态缺少有效账号标识；请重新登录 WorkBuddy")
    raw_token = auth.get("accessToken")
    kind = _token_format(raw_token)
    token = raw_token if kind == "plaintext" else _run_helper({"operation": "decrypt", "value": raw_token}).get("accessToken")
    if not _token_valid(token):
        raise CredentialError("INVALID_TOKEN", "WorkBuddy 登录态解密结果无效")
    enterprise = account.get("enterpriseId")
    domain = auth.get("domain")
    return Session(
        token=token,
        uid=uid,
        domain=domain if isinstance(domain, str) and printable.fullmatch(domain) else None,
        enterprise_id=enterprise if isinstance(enterprise, str) and printable.fullmatch(enterprise) else None,
        api_base=_api_base(auth),
        credential_format=kind,
    )
