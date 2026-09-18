"""API 密钥存储：一律走系统凭据管理器（设计 §10），明文不落项目/配置文件。"""
from __future__ import annotations

_SERVICE = "AITranslator"


def set_api_key(provider_id: str, key: str) -> None:
    import keyring
    keyring.set_password(_SERVICE, provider_id, key)


def get_api_key(provider_id: str) -> str:
    import keyring
    key = keyring.get_password(_SERVICE, provider_id)
    if not key:
        raise KeyError(f"未配置 {provider_id} 的 API 密钥，请先在设置页填写")
    return key


def delete_api_key(provider_id: str) -> None:
    import keyring
    try:
        keyring.delete_password(_SERVICE, provider_id)
    except Exception:
        pass


def test_backend() -> str:
    """连通性自检：写入再读回。"""
    import keyring
    keyring.set_password(_SERVICE, "__selftest__", "ok")
    value = keyring.get_password(_SERVICE, "__selftest__")
    keyring.delete_password(_SERVICE, "__selftest__")
    return "ok" if value == "ok" else "failed"
