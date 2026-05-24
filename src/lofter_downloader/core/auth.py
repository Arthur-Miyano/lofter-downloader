"""登录模块。

管理 LOFTER 登录会话，支持 Cookie 导入和会话持久化。
"""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from lofter_downloader.config import settings
from lofter_downloader.utils.logger import setup_logger

logger = setup_logger(__name__)


class AuthManager:
    """登录管理器。

    负责 Cookie 的导入、验证、加密存储和加载。

    Parameters
    ----------
    key_file : Path or None
        加密密钥文件路径，默认在数据库目录下
    """

    SALT = b"lofter_downloader_salt"
    SESSION_FILE = "session.enc"
    TOKEN_FILE = "token.enc"

    def __init__(self, key_file: Path | None = None) -> None:
        key_dir = key_file or settings.db_path.parent
        key_dir.mkdir(parents=True, exist_ok=True)
        self._key_path = key_dir / ".cipher_key"
        self._session_path = key_dir / self.SESSION_FILE
        self._fernet: Fernet | None = None

    def _get_cipher(self) -> Fernet:
        """获取或创建加密器。"""
        if self._fernet is not None:
            return self._fernet

        if self._key_path.exists():
            key = self._key_path.read_bytes()
        else:
            key = self._generate_key()
            self._key_path.write_bytes(key)

        self._fernet = Fernet(key)
        return self._fernet

    def _generate_key(self) -> bytes:
        """生成加密密钥。"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.SALT,
            iterations=480000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(b"lofter_secret"))
        return key

    def save_cookie(self, cookie: str) -> None:
        """加密保存 Cookie 到本地文件。

        Parameters
        ----------
        cookie : str
            从浏览器复制的完整 Cookie 字符串
        """
        cipher = self._get_cipher()
        encrypted = cipher.encrypt(cookie.encode("utf-8"))
        self._session_path.write_bytes(encrypted)
        logger.info("Cookie saved securely to: %s", self._session_path)

    def load_cookie(self) -> str | None:
        """从本地文件加载并解密 Cookie。

        Returns
        -------
        str or None
            解密后的 Cookie，文件不存在时返回 None
        """
        if not self._session_path.exists():
            return None

        try:
            cipher = self._get_cipher()
            encrypted = self._session_path.read_bytes()
            cookie = cipher.decrypt(encrypted).decode("utf-8")
            logger.debug("Cookie loaded from: %s", self._session_path)
            return cookie
        except Exception:
            logger.warning("Failed to decrypt saved cookie")
            return None

    def clear_session(self) -> None:
        """清除保存的登录会话。"""
        if self._session_path.exists():
            self._session_path.unlink()
            logger.info("Session cleared")

    def has_session(self) -> bool:
        """检查是否有保存的登录会话。"""
        return self._session_path.exists()

    # --- Token 管理 ---

    def save_token(self, token: str) -> None:
        """加密保存 Token 到本地文件。"""
        cipher = self._get_cipher()
        encrypted = cipher.encrypt(token.encode("utf-8"))
        token_path = self._key_path.parent / self.TOKEN_FILE
        token_path.write_bytes(encrypted)
        logger.info("Token saved securely to: %s", token_path)

    def load_token(self) -> str | None:
        """从本地文件加载并解密 Token。"""
        token_path = self._key_path.parent / self.TOKEN_FILE
        if not token_path.exists():
            return None
        try:
            cipher = self._get_cipher()
            encrypted = token_path.read_bytes()
            token = cipher.decrypt(encrypted).decode("utf-8")
            logger.debug("Token loaded from: %s", token_path)
            return token
        except Exception:
            logger.warning("Failed to decrypt saved token")
            return None

    def clear_token(self) -> None:
        """清除保存的 Token。"""
        token_path = self._key_path.parent / self.TOKEN_FILE
        if token_path.exists():
            token_path.unlink()
            logger.info("Token cleared")

    def has_token(self) -> bool:
        """检查是否有保存的 Token。"""
        return (self._key_path.parent / self.TOKEN_FILE).exists()
