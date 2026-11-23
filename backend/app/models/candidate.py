"""考生账号表 candidates。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntUnsigned, CreatedUpdatedMixin, TinyInt


class Candidate(Base, CreatedUpdatedMixin):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(BigIntUnsigned, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="学号/考号，全局唯一")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="考生姓名")
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False, comment="bcrypt 哈希，用于登录校验")
    init_password_enc: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Fernet 加密的初始密码，过期置空"
    )
    init_pwd_expire_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="初始密码可查看截止时间")
    status: Mapped[int] = mapped_column(TinyInt, nullable=False, server_default=text("1"), comment="1 启用 / 0 禁用")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="导入备注")
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    def __repr__(self) -> str:
        return f"<Candidate id={self.id} account={self.account!r}>"
