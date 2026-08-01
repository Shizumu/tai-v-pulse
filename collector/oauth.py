from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Any


AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
YOUTUBE_API_ENDPOINT = "https://www.googleapis.com/youtube/v3"
YOUTUBE_ANALYTICS_ENDPOINT = "https://youtubeanalytics.googleapis.com/v2/reports"
REQUIRED_SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)
APP_ENTROPY = b"TaiVPulse OAuth credentials v1"


class OAuthError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise OAuthError("OAuth 憑證加密目前只支援 Windows")
    input_blob, input_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(APP_ENTROPY)
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob), "Tai V Pulse", ctypes.byref(entropy_blob), None, None,
        0x01, ctypes.byref(output_blob),
    ):
        raise OAuthError("Windows 無法加密 OAuth 憑證")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
        del input_buffer, entropy_buffer


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise OAuthError("OAuth 憑證解密目前只支援 Windows")
    input_blob, input_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(APP_ENTROPY)
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), None, ctypes.byref(entropy_blob), None, None,
        0x01, ctypes.byref(output_blob),
    ):
        raise OAuthError("OAuth 憑證無法由目前的 Windows 使用者解密")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
        del input_buffer, entropy_buffer


class EncryptedJsonStore:
    def __init__(self, path: Path):
        self.path = path

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> dict[str, Any] | None:
        if not self.exists():
            return None
        try:
            value = json.loads(_unprotect(self.path.read_bytes()).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OAuthError("本機 OAuth 憑證檔已損壞或無法讀取") from error
        if not isinstance(value, dict):
            raise OAuthError("本機 OAuth 憑證格式無效")
        return value

    def write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_bytes(_protect(json.dumps(value, separators=(",", ":")).encode("utf-8")))
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def delete(self) -> None:
        if self.path.exists():
            self.path.unlink()


def _safe_google_error(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8", errors="replace"))
        detail = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            return str(detail.get("message") or detail.get("status") or "Google API 拒絕要求")[:240]
        if isinstance(detail, str):
            return detail[:240]
    except (json.JSONDecodeError, OSError):
        pass
    return f"Google API 回傳 HTTP {error.code}"


def oauth_error_guidance(message: str) -> dict[str, Any]:
    raw = str(message or "").strip()
    normalized = raw.casefold()
    if (
        "youtube analytics api has not been used" in normalized
        or "youtubeanalytics.googleapis.com" in normalized and "disabled" in normalized
        or "accessnotconfigured" in normalized
        or "service_disabled" in normalized
    ):
        return {
            "code": "analytics_api_disabled",
            "title": "尚未啟用 YouTube Analytics API",
            "message": "OAuth 已連線，但 Google Cloud 專案還不能呼叫私人 Analytics。",
            "steps": [
                "開啟 YouTube Analytics API 頁面。",
                "確認右上角選到匯出這份 OAuth JSON 的同一個專案。",
                "按下「啟用」，等待 1～5 分鐘讓設定生效。",
                "回到台V Pulse 再按一次「立即同步」。",
            ],
            "help_url": "https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com",
            "help_label": "開啟 YouTube Analytics API",
        }
    if "access_denied" in normalized or "not authorized to use this app" in normalized:
        return {
            "code": "oauth_test_user_missing",
            "title": "目前帳號沒有 OAuth 測試資格",
            "message": "應用程式仍在測試階段，只有 Google Cloud 測試使用者可以授權。",
            "steps": [
                "在 Google Cloud 選擇建立 OAuth JSON 的同一個專案。",
                "進入 Google Auth Platform 的「目標對象（Audience）」。",
                "把目前登入的完整 Google 帳號加入「測試使用者」並儲存。",
                "等待一兩分鐘，再重新按「連結我的 YouTube 頻道」。",
            ],
            "help_url": "https://console.cloud.google.com/auth/audience",
            "help_label": "開啟 OAuth 目標對象設定",
        }
    if (
        "invalid_grant" in normalized
        or "token has been expired or revoked" in normalized
        or "token has been revoked" in normalized
        or "invalid credentials" in normalized
    ):
        return {
            "code": "oauth_expired",
            "title": "Google 授權已過期或被撤銷",
            "message": "測試模式的 refresh token 通常 7 天後失效，也可能由帳號安全設定撤銷。",
            "steps": [
                "在台V Pulse 中斷目前連線。",
                "重新按「連結我的 YouTube 頻道」完成授權。",
                "若要長期每日同步，請將 Google OAuth 發布狀態改為正式環境。",
            ],
            "help_url": "https://console.cloud.google.com/auth/audience",
            "help_label": "檢查 OAuth 發布狀態",
        }
    if "insufficient" in normalized and ("scope" in normalized or "permission" in normalized):
        return {
            "code": "oauth_scope_missing",
            "title": "Google 唯讀權限不完整",
            "message": "目前 token 沒有同時取得 YouTube 帳戶與 Analytics 唯讀權限。",
            "steps": [
                "中斷目前連線並重新授權。",
                "在 Google 同意畫面保留兩項唯讀權限。",
                "確認 OAuth 專案已啟用 YouTube Data API v3 與 YouTube Analytics API。",
            ],
            "help_url": "https://console.cloud.google.com/auth/scopes",
            "help_label": "檢查 OAuth 資料存取範圍",
        }
    if "quota" in normalized or "dailylimitexceeded" in normalized or "rate limit" in normalized:
        return {
            "code": "google_quota_exceeded",
            "title": "Google API 暫時達到限制",
            "message": "目前要求被 Google 的配額或速率限制暫停。",
            "steps": [
                "先停止重複按同步，稍後再試。",
                "在 Google Cloud 的 API 指標與配額頁確認限制。",
                "不要為了重試而建立或分享新的憑證。",
            ],
            "help_url": "https://console.cloud.google.com/apis/dashboard",
            "help_label": "查看 API 狀態與配額",
        }
    if "無法連線 google" in normalized or "timed out" in normalized or "timeout" in normalized:
        return {
            "code": "google_unreachable",
            "title": "目前無法連線 Google",
            "message": "網路、DNS、防火牆或 Google 服務暫時沒有回應。",
            "steps": [
                "確認瀏覽器能開啟 Google Cloud 與 YouTube。",
                "檢查 VPN、防火牆或代理伺服器是否阻擋 Google API。",
                "恢復連線後再按一次「立即同步」。",
            ],
            "help_url": None,
            "help_label": None,
        }
    return {
        "code": "oauth_sync_failed",
        "title": "YouTube Analytics 同步未完成",
        "message": "Google 回傳了尚未分類的錯誤；可展開技術細節核對原文。",
        "steps": [
            "確認兩個 YouTube API 都在同一個 OAuth 專案中啟用。",
            "確認登入的是實際管理該 YouTube 頻道的 Google 帳號。",
            "若重新同步仍失敗，可保留技術細節並匯出診斷報告。",
        ],
        "help_url": None,
        "help_label": None,
    }


class GoogleOAuth:
    def __init__(self, secret_directory: Path, redirect_uri: str):
        self.client_store = EncryptedJsonStore(secret_directory / "youtube-oauth-client.dat")
        self.token_store = EncryptedJsonStore(secret_directory / "youtube-oauth-token.dat")
        self.redirect_uri = redirect_uri
        self.pending: dict[str, tuple[float, str]] = {}
        self.lock = threading.RLock()

    def configure_client(self, raw: bytes) -> None:
        if len(raw) > 128 * 1024:
            raise ValueError("OAuth JSON 不可超過 128 KB")
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("無法解析 Google OAuth JSON") from error
        installed = payload.get("installed") if isinstance(payload, dict) else None
        if not isinstance(installed, dict):
            raise ValueError("請使用 Google Cloud 的『桌面應用程式』OAuth JSON")
        client_id = str(installed.get("client_id") or "").strip()
        client_secret = str(installed.get("client_secret") or "").strip()
        if not client_id.endswith(".apps.googleusercontent.com"):
            raise ValueError("OAuth JSON 缺少有效的桌面應用程式 client_id")
        self.client_store.write({"client_id": client_id, "client_secret": client_secret})

    def status(self) -> dict[str, Any]:
        credential_error = ""
        try:
            token = self.token_store.read() if self.token_store.exists() else None
        except OAuthError as error:
            token = None
            credential_error = str(error)
        return {
            "configured": self.client_store.exists(),
            "authorized": bool(token and token.get("refresh_token")),
            "scopes": sorted(str(token.get("scope") or "").split()) if token else [],
            "credential_error": credential_error,
        }

    def authorization_url(self) -> str:
        client = self.client_store.read()
        if not client:
            raise ValueError("請先匯入 Google 桌面應用程式 OAuth JSON")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        with self.lock:
            now = time.time()
            self.pending = {
                key: value for key, value in self.pending.items() if value[0] > now
            }
            self.pending[state] = (now + 600, verifier)
        query = urllib.parse.urlencode({
            "client_id": client["client_id"],
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(REQUIRED_SCOPES),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
        })
        return f"{AUTHORIZATION_ENDPOINT}?{query}"

    def complete_authorization(self, code: str, state: str) -> dict[str, Any]:
        with self.lock:
            pending = self.pending.pop(state, None)
        if not pending or pending[0] <= time.time():
            raise ValueError("授權狀態已失效，請回到台V Pulse 重新連結")
        if not code:
            raise ValueError("Google 沒有回傳授權碼")
        client = self.client_store.read()
        if not client:
            raise ValueError("本機 OAuth 設定已不存在")
        token = self._form_request(TOKEN_ENDPOINT, {
            "client_id": client["client_id"],
            "client_secret": client.get("client_secret", ""),
            "code": code,
            "code_verifier": pending[1],
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        })
        granted = set(str(token.get("scope") or "").split())
        missing = set(REQUIRED_SCOPES) - granted
        if missing:
            raise ValueError("Google 未授予台V Pulse 所需的兩項唯讀權限")
        if not token.get("refresh_token"):
            raise ValueError("Google 未回傳持續授權；請移除舊授權後重新連結")
        token["expires_at"] = time.time() + float(token.get("expires_in") or 3600)
        token["scope"] = " ".join(sorted(granted))
        channel_payload = self.authorized_json(
            f"{YOUTUBE_API_ENDPOINT}/channels?part=snippet,statistics,contentDetails&mine=true",
            token_override=token,
        )
        items = channel_payload.get("items") if isinstance(channel_payload, dict) else None
        if not isinstance(items, list) or len(items) != 1:
            raise ValueError("這個 Google 帳戶沒有唯一可用的 YouTube 頻道")
        self.token_store.write(token)
        return items[0]

    def _form_request(self, endpoint: str, values: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            endpoint,
            data=urllib.parse.urlencode(values).encode("ascii"),
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise OAuthError(_safe_google_error(error)) from error
        except urllib.error.URLError as error:
            raise OAuthError(f"無法連線 Google：{error.reason}") from error
        if not isinstance(payload, dict):
            raise OAuthError("Google 回傳無效資料")
        return payload

    def access_token(self, token_override: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
        token = dict(token_override or self.token_store.read() or {})
        if not token.get("refresh_token"):
            raise ValueError("尚未連結 YouTube 頻道")
        if token.get("access_token") and float(token.get("expires_at") or 0) > time.time() + 90:
            return str(token["access_token"]), token
        client = self.client_store.read()
        if not client:
            raise ValueError("OAuth 用戶端設定已不存在")
        refreshed = self._form_request(TOKEN_ENDPOINT, {
            "client_id": client["client_id"],
            "client_secret": client.get("client_secret", ""),
            "refresh_token": token["refresh_token"],
            "grant_type": "refresh_token",
        })
        token.update(refreshed)
        token["expires_at"] = time.time() + float(refreshed.get("expires_in") or 3600)
        if token_override is None:
            self.token_store.write(token)
        return str(token["access_token"]), token

    def authorized_json(
        self, url: str, *, token_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        access_token, _ = self.access_token(token_override)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "TaiVPulse/0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise OAuthError(_safe_google_error(error)) from error
        except urllib.error.URLError as error:
            raise OAuthError(f"無法連線 Google：{error.reason}") from error
        if not isinstance(payload, dict):
            raise OAuthError("Google 回傳無效資料")
        return payload

    def analytics_report(
        self,
        start_date: str,
        end_date: str,
        metrics: str,
        *,
        dimensions: str = "",
        sort: str = "",
        max_results: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "ids": "channel==MINE",
            "startDate": start_date,
            "endDate": end_date,
            "metrics": metrics,
        }
        if dimensions:
            params["dimensions"] = dimensions
        if sort:
            params["sort"] = sort
        if max_results is not None:
            params["maxResults"] = max_results
        return self.authorized_json(f"{YOUTUBE_ANALYTICS_ENDPOINT}?{urllib.parse.urlencode(params)}")

    def video_details(self, video_ids: list[str]) -> list[dict[str, Any]]:
        normalized = list(dict.fromkeys(video_id.strip() for video_id in video_ids if video_id.strip()))
        items: list[dict[str, Any]] = []
        for offset in range(0, len(normalized), 50):
            params = urllib.parse.urlencode({
                "part": "snippet,liveStreamingDetails",
                "id": ",".join(normalized[offset:offset + 50]),
                "maxResults": 50,
            })
            payload = self.authorized_json(f"{YOUTUBE_API_ENDPOINT}/videos?{params}")
            batch = payload.get("items") if isinstance(payload, dict) else None
            if isinstance(batch, list):
                items.extend(item for item in batch if isinstance(item, dict))
        return items

    def revoke_and_delete(self) -> str | None:
        token = self.token_store.read() if self.token_store.exists() else None
        warning: str | None = None
        if token and token.get("refresh_token"):
            try:
                request = urllib.request.Request(
                    REVOKE_ENDPOINT,
                    data=urllib.parse.urlencode({"token": token["refresh_token"]}).encode("ascii"),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=20):
                    pass
            except (urllib.error.HTTPError, urllib.error.URLError) as error:
                warning = f"Google 撤銷要求未完成：{getattr(error, 'reason', '網路或服務錯誤')}"
        self.token_store.delete()
        return warning

    def delete_client(self) -> None:
        if self.token_store.exists():
            raise ValueError("請先中斷頻道連線，再刪除 OAuth 設定")
        self.client_store.delete()
