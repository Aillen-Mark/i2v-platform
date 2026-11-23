"""管理端路由包（/admin/*）。"""

from app.admin import auth, candidates, questions

__all__ = ["auth", "candidates", "questions"]
