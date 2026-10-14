# /* StarOfCAU-BEGIN: Authentication & Role-Based Access Control (RBAC) */
"""
小微电商对账系统 - 用户鉴权与角色权限引擎
作者: StarOfCAU (李锐)
安全特性:
1. 采用 SHA-256 + 动态盐 (Salt) 进行密码哈希，杜绝明文存储
2. 内存 Session 会话令牌（支持过期与角色校验）
3. 三种企业角色：
   - auditor (事务所审计员): 查账、审验差异、导出调节平衡表
   - operator (电商操盘手/店长): 实时录入单据、发货登记、触发核算
   - admin (系统管理员): 全量权限、重置清库、压测生成
"""

import hashlib
import os
import uuid
import time
from typing import Optional, Dict, Any
from .db import DatabaseManager


class AuthManager:
    # 内存 Session 存储: {token: {"username": ..., "role": ..., "expires_at": ...}}
    _sessions: Dict[str, Dict[str, Any]] = {}
    SESSION_EXPIRE_SECONDS = 86400  # 24小时有效

    def __init__(self, db: DatabaseManager):
        self.db = db
        self.ensure_default_users()

    @staticmethod
    def hash_password(password: str, salt: str) -> str:
        return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()

    def ensure_default_users(self):
        """确保系统内置三个不同角色的测试账号"""
        default_users = [
            ("auditor", "auditor123", "auditor", "年审主审审计员(StarOfCAU)"),
            ("operator", "operator123", "operator", "星澜电商运营主管"),
            ("admin", "admin123", "admin", "系统超级管理员(老默)")
        ]

        with self.db.get_connection() as conn:
            for username, pwd, role, full_name in default_users:
                row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
                if not row:
                    salt = os.urandom(16).hex()
                    pwd_hash = self.hash_password(pwd, salt)
                    conn.execute("""
                    INSERT INTO users (username, password_hash, salt, role, full_name, created_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                    """, (username, pwd_hash, salt, role, full_name))
            conn.commit()

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """用户登录校验，返回 session token 与用户元数据"""
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not row:
                return None
            
            pwd_hash = self.hash_password(password, row["salt"])
            if pwd_hash != row["password_hash"]:
                return None

            token = f"TOKEN_{uuid.uuid4().hex}"
            session_data = {
                "token": token,
                "username": row["username"],
                "role": row["role"],
                "full_name": row["full_name"],
                "expires_at": time.time() + self.SESSION_EXPIRE_SECONDS
            }
            self._sessions[token] = session_data
            
            # 记录审计日志
            self.db.record_audit_log(row["username"], "USER_LOGIN", f"用户成功登录，角色: {row['role']}")
            return session_data

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """验证会话令牌有效性"""
        if not token:
            return None
        session = self._sessions.get(token)
        if not session:
            return None
        if time.time() > session["expires_at"]:
            del self._sessions[token]
            return None
        return session

    def logout(self, token: str):
        if token in self._sessions:
            del self._sessions[token]

# /* StarOfCAU-END */
